import ApplicationServices
import Foundation

/// One element read in a single Accessibility call: role, subrole, labels, value and children.
struct AXNode {
    /// Which attributes to read: roles only, roles and labels, or everything the walk needs.
    enum Fields { case roles, labels, texts }

    let role: String?
    let subrole: String?
    let title: String?
    let description: String?
    let value: String?
    let children: [AXUIElement]
    /// The element reported no children attribute (allowed for leaf roles only).
    let childrenAbsent: Bool
    /// The children read failed for a reason other than "no children".
    let childrenFailed: Bool

    /// True when the element is a secure text field.
    var isSecure: Bool { AX.isSecure(role, subrole) }

    /// The attribute names for each field set; children always last.
    static func names(_ fields: Fields) -> [String] {
        switch fields {
        case .roles: return ["AXRole", "AXSubrole", "AXChildren"]
        case .labels: return ["AXRole", "AXSubrole", "AXTitle", "AXDescription", "AXChildren"]
        case .texts: return ["AXRole", "AXSubrole", "AXTitle", "AXDescription", "AXValue", "AXChildren"]
        }
    }

    /// Reads one element; nil when the call itself fails.
    static func read(_ e: AXUIElement, _ fields: Fields) -> AXNode? {
        let names = names(fields)
        guard let slots = AX.multiple(e, names) else { return nil }
        func text(_ name: String) -> String? {
            guard let i = names.firstIndex(of: name) else { return nil }
            return slots[i].value as? String
        }
        let kids = slots[names.count - 1]
        let children = AX.asElements(kids.value)
        let absent = children == nil && AX.isAbsent(kids.error)
        return AXNode(role: text("AXRole"), subrole: text("AXSubrole"), title: text("AXTitle"),
                      description: text("AXDescription"), value: text("AXValue"), children: children ?? [],
                      childrenAbsent: absent, childrenFailed: children == nil && !absent)
    }
}
