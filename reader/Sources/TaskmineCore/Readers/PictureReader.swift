import ApplicationServices
import CoreGraphics
import Foundation
import Vision

/// The picture reader: OCR of the page area when a checked window shows little text (reader spec 7).
@MainActor public final class PictureReader: @MainActor Reader {
    public let provenance = "screen_ocr"
    private let text: TextReader
    private var lastByWindow: [String: Date] = [:]
    private var lastByHash: [String: Date] = [:]
    private var lastAny: Date?
    /// Why the last attempt gave nothing: a fixed code for the status file.
    public private(set) var lastStatus = "idle"

    /// A picture reader fed by the text reader's candidates and re-checks.
    init(text: TextReader) { self.text = text }

    /// Throws when Screen Recording is not granted; never prompts.
    public func start() throws {
        guard MacSession.screenCaptureGranted else { throw ReaderError.screenRecordingUnavailable }
    }

    /// Forgets the rate-limit state.
    public func stop() {
        lastByWindow = [:]
        lastByHash = [:]
        lastAny = nil
    }

    /// True when the text reader holds a canvas candidate for this window.
    func hasCanvasCandidate(_ window: WindowRef) -> Bool {
        text.ocrCandidate(for: window)?.reason == "canvas_document"
    }

    /// True when the text reader holds any candidate for this window.
    func hasCandidate(_ window: WindowRef) -> Bool { text.ocrCandidate(for: window) != nil }

    /// OCR of the window's page area, or nil when a gate, a limit or a re-check refuses it.
    public func snapshot(of window: WindowRef) -> Reading? {
        guard let candidate = text.ocrCandidate(for: window) else { return none("no_candidate") }
        let s = candidate.surface
        guard text.gate.appCheck(s.app, provenance: provenance) == nil else { return none("policy") }
        guard MacSession.screenCaptureGranted else { return none("screen_recording_unavailable") }
        let now = Date()
        guard admit(candidate, now) else { return none("rate_limited") }
        guard text.stillValid(s), let frame = AX.frame(s.webArea ?? s.window), let windowFrame = AX.frame(s.window),
              let id = ScreenCapture.windowID(pid: s.app.pid, frame: windowFrame) else { return none("capture_invalidated") }
        let bars = barFrames(s.window)
        let lines = recognise(id: id, rect: frame, bars: bars)
        guard text.stillValid(s) else { return none("capture_invalidated") }
        guard let lines, !lines.text.isEmpty,
              let hidden = TextSafety.evaluate(lines.text, mode: .text, limit: 16_384).text, !hidden.isEmpty else {
            return none("no_text")
        }
        lastStatus = "captured"
        var metadata = s.metadata
        metadata["capture_method"] = "content_ocr"
        metadata["trigger"] = "content_fallback"
        metadata["ocr_reason"] = candidate.reason
        metadata["ocr_confidence"] = String(format: "%.3f", lines.confidence)
        return Reading(title: s.title, text: hidden, metadata: metadata, provenance: provenance, kind: "screen_text")
    }

    /// Rate limits of spec 7.4: one per window per 10 s, same text per 30 s, one overall per 3 s.
    private func admit(_ candidate: OCRCandidate, _ now: Date) -> Bool {
        let ref = candidate.surface.windowRef
        if let lastAny, now.timeIntervalSince(lastAny) < 3 { return false }
        if let last = lastByWindow[ref], now.timeIntervalSince(last) < 10 { return false }
        if let last = lastByHash[candidate.textHash], now.timeIntervalSince(last) < 30 { return false }
        lastAny = now
        lastByWindow[ref] = now
        lastByHash[candidate.textHash] = now
        if lastByHash.count > 256 { lastByHash = lastByHash.filter { now.timeIntervalSince($0.value) < 30 } }
        return true
    }

    /// Captures, recognises and releases the image; lines inside toolbars and menus are dropped.
    private func recognise(id: CGWindowID, rect: CGRect, bars: [CGRect]) -> (text: String, confidence: Double)? {
        let observations: [VNRecognizedTextObservation]
        do {
            guard let image = ScreenCapture.image(window: id, rect: rect) else { return nil }
            let request = VNRecognizeTextRequest()
            request.recognitionLevel = .accurate
            request.usesLanguageCorrection = false
            request.minimumTextHeight = 0.008
            try VNImageRequestHandler(cgImage: image, options: [:]).perform([request])
            observations = request.results ?? []
        } catch {
            return nil
        }
        var lines: [OCRLines.Line] = []
        var confidence = 0.0
        for observation in observations {
            guard let top = observation.topCandidates(1).first else { continue }
            let box = observation.boundingBox
            let screen = CGRect(x: rect.minX + box.minX * rect.width, y: rect.minY + (1 - box.maxY) * rect.height,
                                width: box.width * rect.width, height: box.height * rect.height)
            if bars.contains(where: { $0.contains(CGPoint(x: screen.midX, y: screen.midY)) }) { continue }
            lines.append(OCRLines.Line(text: top.string, box: screen))
            confidence += Double(top.confidence)
        }
        guard !lines.isEmpty else { return ("", 0) }
        return (OCRLines.join(lines), confidence / Double(lines.count))
    }

    /// Frames of toolbars, menu bars and menus near the top of the window tree.
    private func barFrames(_ window: AXUIElement) -> [CGRect] {
        var out: [CGRect] = []
        var level: [AXUIElement] = [window]
        for _ in 0..<3 {
            var next: [AXUIElement] = []
            for element in level {
                guard let node = AXNode.read(element, .roles) else { continue }
                if let role = node.role, WebSurfaces.shared.furniture.containerRoles.contains(role) {
                    if let frame = AX.frame(element) { out.append(frame) }
                    continue
                }
                next.append(contentsOf: node.children.prefix(64))
            }
            level = next
        }
        return out
    }

    /// Records why nothing was read.
    private func none(_ status: String) -> Reading? {
        lastStatus = status
        return nil
    }
}
