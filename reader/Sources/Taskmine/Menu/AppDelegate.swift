import AppKit
import TaskmineCore

/// Opens the files, builds the registry and the menu, and stops capture before quit.
@MainActor final class AppDelegate: NSObject, NSApplicationDelegate {
    private var registry: Registry?
    private var menu: MenuController?

    /// Starts paused; never prompts for a permission.
    func applicationDidFinishLaunching(_ notification: Notification) {
        let paths = Paths.fromEnvironment()
        do {
            let registry = try Registry(paths: paths)
            self.registry = registry
            menu = MenuController(registry: registry)
            registry.launch()
        } catch {
            FileHandle.standardError.write(Data("taskmine: cannot open the files in the home folder\n".utf8))
            NSApp.terminate(nil)
        }
    }

    /// Stops the readers and writes the final counts.
    func applicationWillTerminate(_ notification: Notification) {
        registry?.shutdown()
    }
}
