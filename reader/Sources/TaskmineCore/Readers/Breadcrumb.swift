import ApplicationServices
import Foundation

/// The focus breadcrumb of an interaction: role, name, named ancestors, element id; never a value (spec 2.7).
struct Breadcrumb {
    var controlRole: String?
    var focusName: String?
    var focusPath: String?
    var elementID: String?
    /// The element or an ancestor is a secure field.
    var secure = false

    /// The metadata pairs that are set.
    var metadata: [String: String] {
        var out: [String: String] = [:]
        out["control_role"] = controlRole
        out["focus_name"] = focusName
        out["focus_path"] = focusPath
        out["element_id"] = elementID
        return out
    }

    /// Reads the breadcrumb of `element` in an app called `appName`.
    static func read(_ element: AXUIElement, appName: String) -> Breadcrumb {
        let role = AX.string(element, "AXRole")
        var crumb = Breadcrumb(controlRole: role.map { clean($0, 64) }.flatMap { $0.isEmpty ? nil : $0 })
        if AX.isSecure(role, AX.string(element, "AXSubrole")) { crumb.secure = true; return crumb }
        let name = focusName(element)
        guard let ancestors = ancestors(element, name: name, appName: appName) else { crumb.secure = true; return crumb }
        let labels = ([name].compactMap { $0 } + ancestors)
        guard !labels.contains(where: { TextSafety.evaluate($0, mode: .title).text != $0 }) else { return crumb }
        crumb.focusName = name
        crumb.focusPath = ancestors.isEmpty ? nil : clip(ancestors.joined(separator: " > "), 400)
        crumb.elementID = elementID(role: crumb.controlRole, name: name, identifier: AX.string(element, "AXIdentifier"))
        return crumb
    }

    /// The first usable of title, description, the title element's value, placeholder; at most 80 scalars.
    private static func focusName(_ e: AXUIElement) -> String? {
        let titleElement = AX.element(e, "AXTitleUIElement").flatMap { AX.string($0, "AXValue") }
        let candidates = [AX.string(e, "AXTitle"), AX.string(e, "AXDescription"), titleElement,
                          AX.string(e, "AXPlaceholderValue")]
        return candidates.compactMap { $0.map { clean($0, 80) } }.first { !$0.isEmpty }
    }

    /// Up to three named ancestors, nearest first; nil when an ancestor is secure.
    private static func ancestors(_ e: AXUIElement, name: String?, appName: String) -> [String]? {
        let roles = WebSurfaces.shared.breadcrumbRoles
        var out: [String] = []
        var current = AX.element(e, "AXParent")
        var hops = 0
        while let parent = current, hops < 12 {
            hops += 1
            let role = AX.string(parent, "AXRole")
            if AX.isSecure(role, AX.string(parent, "AXSubrole")) { return nil }
            if role == "AXApplication" { break }
            if let role, roles.contains(role), out.count < 3,
               let label = [AX.string(parent, "AXTitle"), AX.string(parent, "AXDescription")]
                   .compactMap({ $0.map { clean($0, 80) } }).first(where: { !$0.isEmpty }),
               label != name, label != appName, label != out.last {
                out.append(label)
            }
            if role == "AXWindow" { break }
            current = AX.element(parent, "AXParent")
        }
        return out
    }

    /// `type:<role> name:"<name>" id:"<id>"`; a part the safety rules would change is dropped.
    private static func elementID(role: String?, name: String?, identifier: String?) -> String? {
        let id = identifier.flatMap { value -> String? in
            value.unicodeScalars.count <= 80 && !value.unicodeScalars.contains { $0.properties.generalCategory == .control }
                ? value : nil
        }
        let parts = [role.map { "type:\($0)" }, name.map { "name:\"\(quoteless($0))\"" },
                     id.map { "id:\"\(quoteless($0))\"" }].compactMap { $0 }.filter(isSafe)
        guard !parts.isEmpty else { return nil }
        let whole = clip(parts.joined(separator: " "), 512)
        return isSafe(whole) ? whole : nil
    }

    /// True when no safety rule would change the text.
    private static func isSafe(_ text: String) -> Bool {
        TextSafety.evaluate(text, mode: .title).text == text && TextSafety.evaluate(text).text == text
            && !TextSafety.containsCredential(text)
    }

    /// Double quotes become single quotes.
    private static func quoteless(_ text: String) -> String { text.replacingOccurrences(of: "\"", with: "'") }

    /// Control characters to spaces, whitespace collapsed, clipped.
    static func clean(_ text: String, _ limit: Int) -> String {
        let spaced = String(String.UnicodeScalarView(text.unicodeScalars.map {
            $0.properties.generalCategory == .control ? " " : $0
        }))
        return clip(spaced.split(whereSeparator: { $0.isWhitespace }).joined(separator: " "), limit)
    }

    /// The first `limit` scalars.
    static func clip(_ text: String, _ limit: Int) -> String {
        String(String.UnicodeScalarView(text.unicodeScalars.prefix(limit)))
    }
}
