import Foundation

/// Why a surface was not captured (reader spec 4.4); the menu explains each one (9.4).
public enum CoverageReason: String, Sendable, CaseIterable, Codable {
    case browserRequiresExtension = "browser_requires_extension"
    case protectedApplication = "protected_application"
    case secureOrUninspectableSurface = "secure_or_uninspectable_surface"
    case sourceDisabled = "source_disabled"
    case permissionUnavailable = "permission_unavailable"
    case noVisibleContext = "no_visible_context"
    case serviceUnavailable = "service_unavailable"
    case canvasNeedsScreenRecording = "canvas_needs_screen_recording"
    case surfaceTooLarge = "surface_too_large"
    case inspectionTimeout = "inspection_timeout"

    /// True when the reason counts as excluded; the others count as unavailable.
    public var isExclusion: Bool {
        switch self {
        case .browserRequiresExtension, .protectedApplication, .secureOrUninspectableSurface, .sourceDisabled,
             .surfaceTooLarge, .inspectionTimeout: return true
        case .permissionUnavailable, .noVisibleContext, .serviceUnavailable, .canvasNeedsScreenRecording: return false
        }
    }

    /// The sentence the menu shows for this reason.
    public var explanation: String {
        switch self {
        case .browserRequiresExtension: return "This browser is not read yet. Use Chrome, Edge, Brave, Arc or Firefox for now."
        case .protectedApplication: return "This surface is excluded to protect credentials or private activity."
        case .secureOrUninspectableSurface: return "This surface is excluded because its privacy could not be verified."
        case .sourceDisabled: return "This source is paused or disabled by its capture settings."
        case .permissionUnavailable: return "The required macOS capture permission is unavailable."
        case .noVisibleContext: return "No permitted visible context is available on this surface."
        case .serviceUnavailable: return "The local service or its capture policy is unavailable."
        case .canvasNeedsScreenRecording:
            return "Google Sheets, Docs and Slides draw their text on a canvas. "
                + "Allow Screen Recording in the menu so the app can read it."
        case .surfaceTooLarge:
            return "This window has more controls than the app can check safely, so it was set aside. "
                + "Click into the document to read only that part."
        case .inspectionTimeout:
            return "This app answered too slowly for the app to check the window safely, so it was set aside."
        }
    }

    /// The reason for a withheld category (reader spec 4.3).
    public static func forWithheld(_ category: String) -> CoverageReason {
        switch category {
        case "secure_surface": return .secureOrUninspectableSurface
        case "surface_too_large": return .surfaceTooLarge
        case "inspection_timeout": return .inspectionTimeout
        default: return .protectedApplication
        }
    }
}
