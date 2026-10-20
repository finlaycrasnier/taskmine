import ApplicationServices
import Foundation

/// The roles-only privacy inspection of a window: never titles or values (reader spec 2.4).
struct PrivacyInspector {
    /// Why an inspection failed.
    enum Failure: String, Error, Sendable {
        case nodeBudget = "node_budget", depthLimit = "depth_limit", unreadable, deadline
        case secureField = "secure_field", unverifiedWebArea = "unverified_web_area"

        /// The withheld category of spec 4.3.
        var withheldCategory: String {
            switch self {
            case .nodeBudget, .depthLimit: return "surface_too_large"
            case .deadline: return "inspection_timeout"
            case .unreadable, .secureField, .unverifiedWebArea: return "secure_surface"
            }
        }
    }

    private let rules = WebSurfaces.shared
    /// Decides whether a web area may appear inside the window.
    let allowsWebArea: (AXUIElement) -> Bool

    /// Inspects the subtree at `root`; nil when it passes.
    func inspect(_ root: AXUIElement, budget: InspectionBudget = .standard) -> Failure? {
        let deadline = Date().addingTimeInterval(budget.seconds)
        var stack: [(element: AXUIElement, depth: Int, cell: Bool, inGrid: Bool)] = [(root, 0, false, false)]
        var nodes = 0, cells = 0
        while let (element, depth, isCell, inGrid) = stack.popLast() {
            if Date() > deadline { return .deadline }
            if isCell { cells += 1 } else { nodes += 1 }
            if nodes > budget.maxNodes || cells > budget.maxCells { return .nodeBudget }
            if depth > budget.maxDepth { return .depthLimit }
            guard let node = AXNode.read(element, .roles), let role = node.role else { return .unreadable }
            if node.isSecure { return .secureField }
            if role == "AXWebArea", !allowsWebArea(element) { return .unverifiedWebArea }
            // A column repeats the cells its table's rows hold; the rows are inspected instead.
            if role == "AXColumn" { continue }
            if node.childrenFailed || (node.childrenAbsent && !rules.leafRoles.contains(role)) { return .unreadable }
            let childInGrid = rules.gridRoles.contains(role) || (inGrid && rules.gridPartRoles.contains(role))
            for child in node.children.reversed() { stack.append((child, depth + 1, childInGrid, childInGrid)) }
        }
        return nil
    }

    /// The content area to read when the whole window is too large (spec 2.4 focused-subtree fallback).
    func fallbackRoot(window: AXUIElement, focused: AXUIElement?, budget: InspectionBudget = .standard)
        -> Result<AXUIElement, Failure> {
        for area in contentAreas(window: window, focused: focused) {
            guard let failure = inspect(area, budget: budget) else { return .success(area) }
            switch failure {
            case .secureField, .unverifiedWebArea, .deadline, .unreadable: return .failure(failure)
            case .nodeBudget, .depthLimit: continue
            }
        }
        return .failure(.nodeBudget)
    }

    /// Up to three content areas around the focused element, nearest first.
    private func contentAreas(window: AXUIElement, focused: AXUIElement?) -> [AXUIElement] {
        var out: [AXUIElement] = []
        var current = focused
        var hops = 0
        while let element = current, hops <= 24, out.count < 3 {
            if CFEqual(element, window) { break }
            if let role = AX.string(element, "AXRole") {
                if role == "AXWindow" || role == "AXApplication" { break }
                if rules.contentAreaRoles.contains(role) { out.append(element) }
            }
            current = AX.element(element, "AXParent")
            hops += 1
        }
        return out
    }
}
