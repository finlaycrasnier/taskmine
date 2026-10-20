import AppKit
import TaskmineCore

/// The status item and its menu (reader spec 9.2, reduced).
@MainActor final class MenuController: NSObject, NSMenuDelegate {
    private let registry: Registry
    private let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
    private let menu = NSMenu()
    private let summary = SummaryView()

    /// A menu for `registry`.
    init(registry: Registry) {
        self.registry = registry
        super.init()
        menu.delegate = self
        menu.minimumWidth = 320
        item.menu = menu
        registry.onChange = { [weak self] in self?.refreshButton() }
        refreshButton()
    }

    /// Rebuilds the menu each time it opens.
    func menuNeedsUpdate(_ menu: NSMenu) {
        menu.removeAllItems()
        let state = registry.state
        summary.show(title: state.title, detail: state.detail)
        let summaryItem = NSMenuItem()
        summaryItem.view = summary
        menu.addItem(summaryItem)
        menu.addItem(.separator())
        menu.addItem(permission("Accessibility permission...", MacSession.accessibilityGranted,
                                #selector(openAccessibility)))
        menu.addItem(permission("Screen Recording permission...", MacSession.screenCaptureGranted,
                                #selector(openScreenRecording)))
        menu.addItem(action("Delete local data...", #selector(deleteLocalData)))
        let login = action("Open at login", #selector(toggleLogin))
        login.state = LoginItem.isEnabled ? .on : .off
        menu.addItem(login)
        menu.addItem(.separator())
        if let primary = state.primaryAction { menu.addItem(action(Self.title(primary), #selector(primaryAction))) }
        menu.addItem(.separator())
        menu.addItem(action("View my activity", #selector(viewActivity)))
        menu.addItem(.separator())
        let version = NSMenuItem(title: "Version \(ReaderVersion.version) (build \(ReaderVersion.build))", action: nil,
                                 keyEquivalent: "")
        version.isEnabled = false
        menu.addItem(version)
        menu.addItem(action("Quit Taskmine", #selector(quit), key: "q"))
    }

    /// The menu title of a primary action.
    private static func title(_ action: RecorderState.Action) -> String {
        switch action {
        case .record: return "Record my activity"
        case .pause: return "Pause recording"
        case .resume: return "Resume recording"
        }
    }

    /// A menu item that targets this controller.
    private func action(_ title: String, _ selector: Selector, key: String = "") -> NSMenuItem {
        let item = NSMenuItem(title: title, action: selector, keyEquivalent: key)
        item.target = self
        return item
    }

    /// A permission item with an Allowed or Not allowed badge.
    private func permission(_ title: String, _ granted: Bool, _ selector: Selector) -> NSMenuItem {
        let item = action(title, selector)
        item.badge = NSMenuItemBadge(string: granted ? "Allowed" : "Not allowed")
        return item
    }

    /// Updates the icon and tooltip.
    private func refreshButton() {
        let state = registry.state
        let symbol = state.isCapturing ? "record.circle" : "pause.circle"
        let image = NSImage(systemSymbolName: symbol, accessibilityDescription: state.tooltip)
        image?.isTemplate = true
        item.button?.image = image
        item.button?.toolTip = state.tooltip
    }

    /// Record, Pause or Resume.
    @objc private func primaryAction() {
        switch registry.state.primaryAction {
        case .pause: registry.pause()
        case .record, .resume: registry.record()
        case nil: break
        }
    }

    /// Opens System Settings at Accessibility; never prompts.
    @objc private func openAccessibility() {
        open("x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility")
    }

    /// Asks macOS once; if not granted at once, opens System Settings at Screen Recording.
    @objc private func openScreenRecording() {
        if MacSession.screenCaptureGranted || CGRequestScreenCaptureAccess() { return }
        open("x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture")
    }

    /// Confirms, then empties the local queue.
    @objc private func deleteLocalData() {
        let alert = NSAlert()
        alert.messageText = "Delete local data?"
        alert.informativeText = "This deletes every reading waiting on this Mac, sent or not. Recording stays paused."
        alert.addButton(withTitle: "Delete")
        alert.addButton(withTitle: "Cancel")
        alert.alertStyle = .warning
        NSApp.activate(ignoringOtherApps: true)
        guard alert.runModal() == .alertFirstButtonReturn else { return }
        do { try registry.deleteLocalData() } catch {
            let failed = NSAlert()
            failed.messageText = "The local data could not be deleted."
            failed.runModal()
        }
    }

    /// Turns Open at login on or off.
    @objc private func toggleLogin() {
        try? LoginItem.set(!LoginItem.isEnabled)
    }

    /// Opens My activity in the browser.
    @objc private func viewActivity() {
        NSWorkspace.shared.open(registry.paths.coreOrigin)
    }

    /// Quits.
    @objc private func quit() {
        NSApp.terminate(nil)
    }

    /// Opens a settings address.
    private func open(_ address: String) {
        if let url = URL(string: address) { NSWorkspace.shared.open(url) }
    }
}
