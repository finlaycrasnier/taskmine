import Foundation

/// Builds the three readers and runs the capture cycle: 1 s timer, 3 s cycle, pipeline, sightings, queue, upload.
@MainActor public final class Registry {
    private static let wasOnKey = "recording.wasOn"

    /// What the menu shows.
    public private(set) var state = RecorderState()
    /// Called after every change the menu may show.
    public var onChange: (() -> Void)?
    /// App readers by bundle ID; consulted before the text reader.
    public let appReader = AppReader()
    public let paths: Paths
    private let gate: SurfaceGate
    private let textReader: TextReader
    private let pictureReader: PictureReader
    private let pipeline: Pipeline
    private var tracker = SightingTracker()
    private let ledger: EventLedger
    private var queue: Queue?
    private let policyClient: PolicyClient
    private let uploader: Uploader
    private let session = SessionMonitor()
    private let sourceID: String
    private var status = StatusReport()
    private var coverage = Coverage()
    private var policy: Policy?
    private var retentionDays = 30
    private var timer: Timer?
    private var lastCycle: Date?
    private var lastPrune: Date?
    private var cycleRunning = false
    private var readersRunning = false
    private var ocrOn = false
    private var currentWindow: String?
    private var lastPrepared: [String: Reading] = [:]
    private let encoder: JSONEncoder = {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
        return encoder
    }()

    /// Opens the home folder, the coding key and the queue.
    public init(paths: Paths) throws {
        self.paths = paths
        try paths.prepareHome()
        let key = try paths.codingKeyHex()
        pipeline = Pipeline(coder: PrivateDataCoder(keyHex: key))
        sourceID = "mac-" + EventContent.sha256("source|" + key).prefix(12)
        queue = try Queue(path: paths.readerDB.path)
        gate = SurfaceGate(corePort: paths.port, wakeJournal: paths.wakeJournal)
        textReader = TextReader(gate: gate)
        pictureReader = PictureReader(text: textReader)
        ledger = EventLedger(sourceID: sourceID)
        policyClient = PolicyClient(origin: paths.coreOrigin)
        let tokenPaths = paths
        uploader = Uploader(origin: paths.coreOrigin, token: { tokenPaths.readToken() })
    }

    /// Starts paused, undoes a crashed run's web wakes, and starts the 1 s timer.
    public func launch() {
        gate.wake.recoverFromJournal()
        state.offSinceRestart = UserDefaults.standard.bool(forKey: Self.wasOnKey)
        state.accessibilityGranted = MacSession.accessibilityGranted
        textReader.onSnapshotDue = { [weak self] trigger in self?.capture(trigger: trigger) }
        textReader.tracker.emit = { [weak self] record in self?.storeInteraction(record) }
        session.onChange = { [weak self] in self?.sync() }
        session.start()
        timer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.tick() }
        }
        tick()
    }

    /// The user chose Record my activity or Resume recording; refused without Accessibility.
    public func record() {
        state.accessibilityGranted = MacSession.accessibilityGranted
        guard state.accessibilityGranted else {
            coverage.record(.permissionUnavailable, at: Date())
            return changed()
        }
        state.userWantsRecording = true
        state.offSinceRestart = false
        state.stopped = nil
        UserDefaults.standard.set(true, forKey: Self.wasOnKey)
        lastCycle = nil
        Task { await policyClient.invalidate() }
        sync()
    }

    /// The user chose Pause recording: readers stop at once; the core is not told.
    public func pause() {
        state.userWantsRecording = false
        state.offSinceRestart = false
        UserDefaults.standard.set(false, forKey: Self.wasOnKey)
        sync()
    }

    /// Empties the local queue; recording stays paused afterwards.
    public func deleteLocalData() throws {
        pause()
        ledger.reset()
        tracker = SightingTracker()
        lastPrepared = [:]
        currentWindow = nil
        queue = nil
        for suffix in ["", "-wal", "-shm"] {
            let path = paths.readerDB.path + suffix
            if FileManager.default.fileExists(atPath: path) { try FileManager.default.removeItem(atPath: path) }
        }
        queue = try Queue(path: paths.readerDB.path)
        writeStatus()
        changed()
    }

    /// Stops everything before quit; the recording flag is kept for the next launch's message.
    public func shutdown() {
        stopReaders()
        session.stop()
        timer?.invalidate()
        timer = nil
        writeStatus()
    }

    /// The 1 s tick: burst timers, permissions, and a cycle every 3 s.
    private func tick() {
        if readersRunning { textReader.tracker.tick() }
        if state.accessibilityGranted != MacSession.accessibilityGranted || state.sessionAvailable != session.isAvailable {
            sync()
        }
        let now = Date()
        guard !cycleRunning, lastCycle.map({ now.timeIntervalSince($0) >= 3 || $0 > now }) ?? true else { return }
        lastCycle = now
        cycleRunning = true
        Task { [weak self] in
            await self?.cycle()
            self?.cycleRunning = false
        }
    }

    /// One cycle: policy, retention, backstop snapshot, OCR fallback, one upload, status.
    private func cycle() async {
        applyPolicy(await policyClient.current())
        prune()
        if readersRunning {
            if textReader.pollShouldWalk() {
                if textReader.admitPoll() { capture(trigger: "poll") }
            } else {
                extendCurrent()
            }
            ocrFallback()
        }
        await upload()
        writeStatus()
        changed()
    }

    /// Takes the cycle's policy and starts or stops the readers.
    private func applyPolicy(_ fetched: Policy?) {
        policy = fetched
        gate.policy = fetched
        state.policyState = fetched?.state
        if let days = fetched?.retentionDays { retentionDays = days }
        if fetched == nil, state.userWantsRecording { coverage.record(.serviceUnavailable, at: Date()) }
        switch fetched?.state {
        case nil: status.connectionState = "offline"
        case .paused: status.connectionState = "paused by server"
        case .active: if status.connectionState != "update needed", status.connectionState != "disconnected" {
            status.connectionState = "connected"
        }
        }
        sync()
    }

    /// Starts or stops the readers to match the state.
    private func sync() {
        state.accessibilityGranted = MacSession.accessibilityGranted
        state.sessionAvailable = session.isAvailable
        if state.isCapturing && !readersRunning {
            do { try textReader.start() } catch { state.accessibilityGranted = false; return changed() }
            ocrOn = (try? pictureReader.start()) != nil
            try? appReader.start()
            readersRunning = true
            capture(trigger: "recording_started")
        } else if !state.isCapturing && readersRunning {
            stopReaders()
        } else if readersRunning {
            textReader.syncInteractions()
        }
        changed()
    }

    /// Stops every reader and writes the final sighting counts.
    private func stopReaders() {
        guard readersRunning else { return }
        textReader.stop()
        pictureReader.stop()
        appReader.stop()
        readersRunning = false
        for key in ledger.openWindows {
            if let entry = tracker.entry(for: key), let event = ledger.flush(windowKey: key, entry: entry) { append(event) }
        }
    }

    /// One snapshot of the frontmost window: app reader, else text reader.
    private func capture(trigger: String) {
        guard state.isCapturing, readersRunning, let app = AppInfo.frontmost() else { return }
        let now = Date()
        guard let window = textReader.frontmostWindow() else { return outcome(textReader.lastOutcome, app, now) }
        if appReader.handles(bundleID: app.bundleID) {
            if let stop = gate.appCheck(app, provenance: appReader.provenance) {
                return outcome(TextReader.outcome(stop), app, now)
            }
            guard var reading = appReader.snapshot(of: window) else { return }
            reading.metadata["trigger"] = trigger
            return store(reading, key: window.ref, prefix: "desktop-", app: app, at: now)
        }
        guard var reading = textReader.snapshot(of: window) else { return outcome(textReader.lastOutcome, app, now) }
        reading.metadata["trigger"] = trigger
        store(reading, key: window.ref, prefix: "desktop-", app: app, at: now)
    }

    /// The picture reader for the window the text reader marked as thin (spec 7.2).
    private func ocrFallback() {
        guard let window = textReader.frontmostWindow(), pictureReader.hasCandidate(window) else { return }
        guard ocrOn || MacSession.screenCaptureGranted else {
            if pictureReader.hasCanvasCandidate(window) { coverage.record(.canvasNeedsScreenRecording, at: Date()) }
            return
        }
        ocrOn = true
        guard let reading = pictureReader.snapshot(of: window) else { return }
        store(reading, key: "ocr|" + window.ref, prefix: "desktop-content-ocr-", app: nil, at: Date(), focus: false)
    }

    /// One more sighting of the unchanged current window, without a walk.
    private func extendCurrent() {
        guard let key = currentWindow, let reading = lastPrepared[key], let app = AppInfo.frontmost() else { return }
        if key.hasPrefix("withheld|") {
            guard key == "withheld|\(app.bundleID)|\(app.pid)",
                  case .withheld = gate.appCheck(app, provenance: textReader.provenance) else { return }
            return observe(reading, key: key, prefix: "desktop-withheld-", at: Date())
        }
        guard textReader.frontmostWindow()?.ref == key else { return }
        observe(reading, key: key, prefix: "desktop-", at: Date())
    }

    /// Records a skipped or withheld surface.
    private func outcome(_ outcome: SnapshotOutcome, _ app: AppInfo, _ now: Date) {
        switch outcome {
        case .captured: return
        case .skipped(let reason): if let reason { coverage.record(reason, at: now) }
        case .withheld(let category):
            coverage.record(.forWithheld(category), at: now)
            let metadata = ["app_bundle_id": app.bundleID, "gap_reason": "withheld:\(category)", "capture_method": "withheld"]
            let gap = Reading(title: "", text: "", metadata: metadata, provenance: textReader.provenance, kind: "capture_gap")
            store(gap, key: "withheld|\(app.bundleID)|\(app.pid)", prefix: "desktop-withheld-", app: nil, at: now)
        }
    }

    /// Pipeline, then sightings, then the queue; a reading the pipeline refuses becomes a withheld gap.
    private func store(_ reading: Reading, key: String, prefix: String, app: AppInfo?, at now: Date, focus: Bool = true) {
        let prepared: Reading
        do { prepared = try pipeline.prepare(reading) } catch {
            if let app { outcome(.withheld("credential_surface"), app, now) }
            return
        }
        if focus, key != currentWindow {
            if let old = currentWindow, let entry = tracker.entry(for: old),
               let event = ledger.flush(windowKey: old, entry: entry) { append(event) }
            currentWindow = key
        }
        observe(prepared, key: key, prefix: prefix, at: now)
        if reading.kind != "capture_gap" {
            coverage.captured()
            status.lastCaptureAt = now
        }
    }

    /// Sighting tracker, then the ledger, then the queue.
    private func observe(_ prepared: Reading, key: String, prefix: String, at now: Date) {
        let previous = tracker.entry(for: key)
        let outcome = tracker.observe(windowKey: key, reading: prepared, at: now)
        guard let entry = tracker.entry(for: key) else { return }
        if outcome == .newEvent, let previous, let event = ledger.flush(windowKey: key, entry: previous) { append(event) }
        if let event = ledger.observed(windowKey: key, reading: prepared, outcome: outcome, entry: entry, prefix: prefix) {
            append(event)
        }
        lastPrepared[key] = prepared
        if lastPrepared.count > 64 { lastPrepared = lastPrepared.filter { tracker.entry(for: $0.key) != nil } }
    }

    /// An interaction through the pipeline into the queue; a refused edit value keeps only the counts.
    private func storeInteraction(_ record: InteractionRecord) {
        var prepared = try? pipeline.prepare(record.reading)
        if prepared == nil, record.burst != nil {
            var countsOnly = record.reading
            countsOnly.text = ""
            prepared = try? pipeline.prepare(countsOnly)
        }
        guard let prepared else { return }
        let event = record.burst.map { ledger.burst(prepared, $0) } ?? ledger.interaction(prepared, at: record.at)
        if let event { append(event) }
    }

    /// Appends one event revision to the queue.
    private func append(_ event: Event) {
        guard let queue else { return }
        do {
            try queue.append(eventID: event.eventID, revision: event.revision, payload: try encoder.encode(event),
                             createdAt: event.observedAt)
        } catch {
            status.lastError = "queue_append_failed"
        }
    }

    /// Deletes queue rows past retention, at most once a minute, even while paused.
    private func prune() {
        let now = Date()
        guard lastPrune.map({ now.timeIntervalSince($0) >= 60 }) ?? true, let queue else { return }
        lastPrune = now
        do { _ = try queue.prune(olderThan: now.addingTimeInterval(-Double(retentionDays) * 86_400)) } catch {
            status.lastError = "prune_failed"
            pause()
        }
    }

    /// Sends one batch when the core's policy is active; classifies the reply.
    private func upload() async {
        guard state.stopped == nil, policy?.state == .active, let queue,
              let batch = try? queue.nextBatch(), let body = try? batch.body(sourceID: sourceID) else { return }
        switch await uploader.send(body, batchID: batch.batchID) {
        case .acked(let ack):
            do {
                try self.queue?.acknowledge(ack)
                coverage.quarantinedCount += ack.rejected.count
                status.lastUploadAt = Date()
                status.connectionState = "connected"
            } catch {
                status.lastError = "invalid_ack"
            }
        case .retry(let code):
            status.lastError = code
            status.connectionState = "offline"
            if state.userWantsRecording { coverage.record(.serviceUnavailable, at: Date()) }
        case .stop(let stop):
            state.stopped = stop
            status.lastError = stop.rawValue
            status.connectionState = stop == .upgradeNeeded ? "update needed" : "disconnected"
            pause()
        }
    }

    /// Writes status.json: counts and fixed codes only.
    private func writeStatus() {
        let now = Date()
        status.updatedAt = now
        status.captureState = state.isCapturing ? "recording" : state.userWantsRecording ? "waiting" : "paused"
        status.accessibilityGranted = state.accessibilityGranted
        status.allowedApps = policy?.allowedApps ?? []
        status.pendingCount = queue?.pendingCount ?? 0
        status.sessionAvailable = state.sessionAvailable
        status.screenCaptureGranted = MacSession.screenCaptureGranted
        status.ocrEnabled = readersRunning && ocrOn
        status.ocrStatus = status.ocrEnabled ? pictureReader.lastStatus : "off"
        status.coverage = coverage
        status.connectionMessage = state.title
        try? status.write(to: paths.status)
    }

    /// Copies coverage into the menu state and tells the menu.
    private func changed() {
        state.lastReason = coverage.lastReason
        state.canvasNotice = coverage.showsCanvasNotice(at: Date())
        state.quarantined = coverage.quarantinedCount
        onChange?()
    }
}
