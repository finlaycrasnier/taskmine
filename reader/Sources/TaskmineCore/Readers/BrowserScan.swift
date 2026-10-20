import ApplicationServices
import Foundation

/// The native part of a browser window: web areas found, toolbar labels, fails closed (reader spec 3.3, 3.5).
struct BrowserScan {
    /// Web areas in order, without entering them.
    var webAreas: [AXUIElement] = []
    /// Short labels for the private-window check; used in memory only.
    var labels: [String] = []
    /// A secure field, an unreadable element or an exhausted budget.
    var failed = false

    /// Scans a window breadth-first: 600 nodes, depth 24.
    static func run(_ window: AXUIElement, maxNodes: Int = 600, maxDepth: Int = 24) -> BrowserScan {
        let rules = WebSurfaces.shared
        var scan = BrowserScan()
        var queue: [(AXUIElement, Int)] = [(window, 0)]
        var index = 0
        while index < queue.count {
            let (element, depth) = queue[index]
            index += 1
            guard index <= maxNodes, depth <= maxDepth, let node = AXNode.read(element, .labels),
                  let role = node.role, !node.isSecure, !node.childrenFailed else {
                scan.failed = true
                return scan
            }
            if role == "AXWebArea" { scan.webAreas.append(element); continue }
            if rules.labelRoles.contains(role), scan.labels.count < 200,
               let label = [node.title, node.description].compactMap({ $0 }).first(where: { !$0.isEmpty }),
               label.unicodeScalars.count <= 64 {
                scan.labels.append(label)
            }
            queue.append(contentsOf: node.children.map { ($0, depth + 1) })
        }
        return scan
    }

    /// The first web area with an http(s) address and a host, else the first with a local file address.
    func page() -> (area: AXUIElement, url: URL)? {
        var file: (AXUIElement, URL)?
        for area in webAreas {
            guard let url = Self.url(of: area) else { continue }
            let scheme = url.scheme?.lowercased()
            if scheme == "http" || scheme == "https", let host = url.host, !host.isEmpty { return (area, url) }
            if scheme == "file", file == nil { file = (area, url) }
        }
        return file
    }

    /// A web area's address: AXURL, else AXDocument.
    static func url(of area: AXUIElement) -> URL? {
        AX.url(area, "AXURL") ?? AX.url(area, "AXDocument")
    }
}
