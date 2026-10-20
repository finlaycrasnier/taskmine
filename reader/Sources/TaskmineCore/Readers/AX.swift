import ApplicationServices
import Foundation

/// Thin reads and writes over the Accessibility API; every failure is nil.
enum AX {
    /// One attribute value.
    static func value(_ e: AXUIElement, _ attribute: String) -> CFTypeRef? {
        var out: CFTypeRef?
        guard AXUIElementCopyAttributeValue(e, attribute as CFString, &out) == .success else { return nil }
        return out
    }

    /// A string attribute.
    static func string(_ e: AXUIElement, _ attribute: String) -> String? {
        value(e, attribute) as? String
    }

    /// A boolean attribute.
    static func bool(_ e: AXUIElement, _ attribute: String) -> Bool? {
        (value(e, attribute) as? NSNumber)?.boolValue
    }

    /// An integer attribute.
    static func int(_ e: AXUIElement, _ attribute: String) -> Int? {
        (value(e, attribute) as? NSNumber)?.intValue
    }

    /// An element attribute.
    static func element(_ e: AXUIElement, _ attribute: String) -> AXUIElement? {
        asElement(value(e, attribute))
    }

    /// An element array attribute.
    static func elements(_ e: AXUIElement, _ attribute: String) -> [AXUIElement]? {
        asElements(value(e, attribute))
    }

    /// A URL attribute stored as a URL or a string.
    static func url(_ e: AXUIElement, _ attribute: String) -> URL? {
        let raw = value(e, attribute)
        if let url = raw as? URL { return url }
        if let text = raw as? String, !text.isEmpty { return URL(string: text) }
        return nil
    }

    /// The element's frame in screen coordinates, top-left origin.
    static func frame(_ e: AXUIElement) -> CGRect? {
        guard let pos = value(e, "AXPosition"), let size = value(e, "AXSize"),
              CFGetTypeID(pos) == AXValueGetTypeID(), CFGetTypeID(size) == AXValueGetTypeID() else { return nil }
        var point = CGPoint.zero, extent = CGSize.zero
        guard AXValueGetValue(unsafeDowncast(pos, to: AXValue.self), .cgPoint, &point),
              AXValueGetValue(unsafeDowncast(size, to: AXValue.self), .cgSize, &extent) else { return nil }
        return CGRect(origin: point, size: extent)
    }

    /// The process that owns an element.
    static func pid(_ e: AXUIElement) -> pid_t? {
        var pid: pid_t = 0
        return AXUIElementGetPid(e, &pid) == .success ? pid : nil
    }

    /// Writes a boolean attribute.
    @discardableResult
    static func set(_ e: AXUIElement, _ attribute: String, _ flag: Bool) -> Bool {
        AXUIElementSetAttributeValue(e, attribute as CFString, (flag ? kCFBooleanTrue : kCFBooleanFalse)) == .success
    }

    /// The application element of a process, with the 0.25 s messaging timeout of spec 2.6.
    static func application(_ pid: pid_t) -> AXUIElement {
        let app = AXUIElementCreateApplication(pid)
        AXUIElementSetMessagingTimeout(app, 0.25)
        return app
    }

    /// Several attributes in one call; each slot is nil when that attribute failed, with its error.
    static func multiple(_ e: AXUIElement, _ attributes: [String]) -> [(value: CFTypeRef?, error: AXError?)]? {
        var out: CFArray?
        let names = attributes as CFArray
        guard AXUIElementCopyMultipleAttributeValues(e, names, AXCopyMultipleAttributeOptions(rawValue: 0), &out)
                == .success, let items = out as? [AnyObject], items.count == attributes.count else { return nil }
        return items.map { item in
            guard CFGetTypeID(item) == AXValueGetTypeID() else { return (item, nil) }
            let axValue = unsafeDowncast(item, to: AXValue.self)
            guard AXValueGetType(axValue) == .axError else { return (item, nil) }
            var error = AXError.failure
            _ = AXValueGetValue(axValue, .axError, &error)
            return (nil, error)
        }
    }

    /// The value as an element, or nil.
    static func asElement(_ raw: CFTypeRef?) -> AXUIElement? {
        guard let raw, CFGetTypeID(raw) == AXUIElementGetTypeID() else { return nil }
        return unsafeDowncast(raw, to: AXUIElement.self)
    }

    /// The value as an element array, or nil.
    static func asElements(_ raw: CFTypeRef?) -> [AXUIElement]? {
        guard let items = raw as? [AnyObject] else { return nil }
        return items.compactMap { asElement($0) }
    }

    /// True when a role or subrole is a secure text field.
    static func isSecure(_ role: String?, _ subrole: String?) -> Bool {
        role == "AXSecureTextField" || subrole == "AXSecureTextField"
    }

    /// True when an error only means the attribute is absent.
    static func isAbsent(_ error: AXError?) -> Bool {
        error == .noValue || error == .attributeUnsupported
    }
}
