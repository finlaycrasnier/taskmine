import ApplicationServices
import Foundation

/// The depth-first text walk of a window or page in reading order (reader spec 2.5).
final class TextWalk {
    private let rules = WebSurfaces.shared
    private let limits: WalkLimits
    /// Checks a web area met inside an Electron window; returns a withheld category to block.
    private let webAreaCheck: ((AXUIElement) -> String?)?
    private var result = WalkResult()
    private var rows = GridRows()
    private var deadline = Date()
    private var nodes = 0
    private var cells = 0
    private var collected = 0
    private var lastText: String?
    private var stopped = false

    /// A walk with these limits.
    init(limits: WalkLimits, webAreaCheck: ((AXUIElement) -> String?)? = nil) {
        self.limits = limits
        self.webAreaCheck = webAreaCheck
    }

    /// Walks the subtree at `root`.
    func run(_ root: AXUIElement) -> WalkResult {
        result = WalkResult()
        rows = GridRows()
        deadline = Date().addingTimeInterval(limits.seconds)
        nodes = 0; cells = 0; collected = 0; lastText = nil; stopped = false
        visit(root, depth: 0, cell: false, gridPart: false, inGrid: false, inBar: false)
        if let line = rows.flush() { keep(line) }
        return result
    }

    /// Visits one element and its children.
    private func visit(_ e: AXUIElement, depth: Int, cell: Bool, gridPart: Bool, inGrid: Bool, inBar: Bool) {
        if stopped { return }
        if Date() > deadline { return stop(gap: "accessibility-timeout", truncated: false) }
        if cell { cells += 1 } else { nodes += 1 }
        if nodes > limits.maxNodes || cells > limits.maxCells { return stop(gap: "accessibility-tree-truncated") }
        guard let node = AXNode.read(e, .texts) else { result.gaps.insert("children-unavailable"); return }
        if node.isSecure { result.blocked = "secure_field"; stopped = true; return }
        let role = node.role ?? ""
        if role == "AXWebArea", let check = webAreaCheck, let reason = check(e) {
            result.blocked = reason
            stopped = true
            return
        }
        if inGrid, readCell(node) { return }
        emitTexts(node, role: role, inBar: inBar)
        // A column repeats cells its table's rows hold; reading it would duplicate and reorder them.
        if role == "AXColumn" { return }
        if node.childrenFailed || (node.childrenAbsent && !isLeaf(role)) { result.gaps.insert("children-unavailable") }
        if node.children.isEmpty { return }
        if depth + 1 > limits.maxDepth {
            result.truncated = true
            result.gaps.insert("accessibility-tree-truncated")
            return
        }
        let isGrid = rules.gridRoles.contains(role)
        let childCell = isGrid || (gridPart && rules.gridPartRoles.contains(role))
        let bar = inBar || rules.furniture.containerRoles.contains(role)
        for child in node.children {
            visit(child, depth: depth + 1, cell: childCell, gridPart: childCell, inGrid: inGrid || isGrid, inBar: bar)
            if stopped { return }
        }
    }

    /// Reads a spreadsheet cell as `ADDRESS: value`; true when the element was a cell.
    private func readCell(_ node: AXNode) -> Bool {
        let label = [node.title, node.description].compactMap { $0?.trimmingCharacters(in: .whitespacesAndNewlines) }
            .first { !$0.isEmpty }
        guard let label, let row = GridRows.rowNumber(label) else { return false }
        let value = (node.value ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        if value.isEmpty { return true }
        if let line = rows.add(address: label, row: row, value: value) { keep(line) }
        return true
    }

    /// The title, description and value of an element, furniture counted and the rest kept.
    private func emitTexts(_ node: AXNode, role: String, inBar: Bool) {
        let control = rules.furniture.controlRoles.contains(role) && node.subrole != rules.furniture.excludedSubrole
        let entry = rules.furniture.entryRoles.contains(role)
        for (text, isValue) in [(node.title, false), (node.description, false), (node.value, true)] {
            guard let raw = text?.trimmingCharacters(in: .whitespacesAndNewlines), !raw.isEmpty else { continue }
            if raw == lastText { continue }
            lastText = raw
            let scalars = raw.unicodeScalars.count
            let furniture = (control || inBar) && scalars <= rules.furniture.maxLabelScalars && !(entry && isValue)
            if furniture {
                result.furnitureScalars += scalars
                continue
            }
            if let line = rows.flush() { keep(line) }
            keep(raw)
            if stopped { return }
        }
    }

    /// Keeps one whole text unless it would cross 1.5 times the stored limit.
    private func keep(_ text: String) {
        let scalars = text.unicodeScalars.count
        if collected + scalars > limits.storedLimit * 3 / 2 { return stop(gap: "accessibility-tree-truncated") }
        collected += scalars
        result.contentScalars += scalars
        result.lines.append(text)
    }

    /// Ends the walk with a gap.
    private func stop(gap: String, truncated: Bool = true) {
        result.gaps.insert(gap)
        if truncated { result.truncated = true }
        stopped = true
    }

    /// True for a role that may report no children.
    private func isLeaf(_ role: String) -> Bool {
        rules.leafRoles.contains(role) || (limits.isWeb && rules.webLeafRolesExtra.contains(role))
    }
}
