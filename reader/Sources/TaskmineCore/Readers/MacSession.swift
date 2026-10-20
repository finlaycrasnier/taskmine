import ApplicationServices
import CoreGraphics
import Foundation

/// Session, idle time, secure input and permission checks; none of them prompts.
public enum MacSession {
    private typealias SecureInputCheck = @convention(c) () -> DarwinBoolean

    /// IsSecureEventInputEnabled, resolved at run time from the already loaded system frameworks.
    private static let secureInputCheck: SecureInputCheck? = {
        guard let symbol = dlsym(UnsafeMutableRawPointer(bitPattern: -2), "IsSecureEventInputEnabled") else { return nil }
        return unsafeBitCast(symbol, to: SecureInputCheck.self)
    }()

    /// True when the console session is active and the screen is not locked.
    public static func isAvailable() -> Bool {
        guard let info = CGSessionCopyCurrentDictionary() as? [String: Any] else { return false }
        let onConsole = (info[kCGSessionOnConsoleKey] as? NSNumber)?.boolValue ?? false
        let locked = (info["CGSSessionScreenIsLocked"] as? NSNumber)?.boolValue ?? false
        return onConsole && !locked
    }

    /// Seconds since the last input event of any kind.
    static func idleSeconds() -> TimeInterval {
        guard let any = CGEventType(rawValue: UInt32.max) else { return 0 }
        return CGEventSource.secondsSinceLastEventType(.combinedSessionState, eventType: any)
    }

    /// True while a password field holds secure input; unknown counts as on.
    static func secureInputOn() -> Bool {
        secureInputCheck.map { $0().boolValue } ?? true
    }

    /// Accessibility permission, checked without a prompt.
    public static var accessibilityGranted: Bool { AXIsProcessTrusted() }

    /// Screen Recording permission, checked without a prompt.
    public static var screenCaptureGranted: Bool { CGPreflightScreenCaptureAccess() }
}
