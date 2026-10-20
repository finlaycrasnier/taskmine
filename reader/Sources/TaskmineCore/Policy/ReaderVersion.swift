import Foundation

/// The app version and the reader header value the core checks.
public enum ReaderVersion {
    /// The app version shown in the menu.
    public static let version = "0.1.0"
    /// The build number shown in the menu.
    public static let build = "1"
    /// The X-Taskmine-Reader header: app version, then the sanitizer version.
    public static var header: String { "\(version)/\(TextSafety.sanitizerVersion)" }
}
