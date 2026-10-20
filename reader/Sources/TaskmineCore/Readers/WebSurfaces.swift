import Foundation

/// data/rules/web_surfaces.json plus the blocklist entries the readers need (reader spec 3).
struct WebSurfaces: Decodable, Sendable {
    /// The furniture rules of spec 2.5.
    struct Furniture: Decodable, Sendable {
        let controlRoles: Set<String>
        let excludedSubrole: String
        let containerRoles: Set<String>
        let entryRoles: Set<String>
        let maxLabelScalars: Int

        enum CodingKeys: String, CodingKey {
            case controlRoles = "control_roles", excludedSubrole = "control_roles_excluding_subrole"
            case containerRoles = "container_roles", entryRoles = "entry_roles", maxLabelScalars = "max_label_scalars"
        }
    }

    /// The Google Docs editors drawn on a canvas.
    struct Canvas: Decodable, Sendable {
        let host: String
        let scheme: String
        let pathPrefixes: [String]

        enum CodingKeys: String, CodingKey { case host, scheme, pathPrefixes = "path_prefixes" }
    }

    /// Unsupported browsers.
    struct Unsupported: Decodable, Sendable {
        let bundleIDs: [String]

        enum CodingKeys: String, CodingKey { case bundleIDs = "bundle_ids" }
    }

    /// Electron detection.
    struct Electron: Decodable, Sendable {
        let relativePath: String

        enum CodingKeys: String, CodingKey { case relativePath = "relative_path" }
    }

    let chromium: [String]
    let gecko: [String]
    let unsupported: Unsupported
    let electron: Electron
    let formFieldRoles: Set<String>
    let gridRoles: Set<String>
    let gridPartRoles: Set<String>
    let leafRoles: Set<String>
    let webLeafRolesExtra: Set<String>
    let furniture: Furniture
    let contentAreaRoles: Set<String>
    let labelRoles: Set<String>
    let breadcrumbRoles: Set<String>
    let canvas: Canvas
    let shortcutKeys: String

    enum CodingKeys: String, CodingKey {
        case chromium = "chromium_browsers", gecko = "gecko_browsers", unsupported = "unsupported_browsers"
        case electron = "electron_detection", formFieldRoles = "form_field_roles", gridRoles = "grid_roles"
        case gridPartRoles = "grid_part_roles", leafRoles = "leaf_roles_may_report_no_children"
        case webLeafRolesExtra = "web_leaf_roles_extra", furniture, contentAreaRoles = "content_area_roles"
        case labelRoles = "label_roles_for_private_window_scan", breadcrumbRoles = "breadcrumb_ancestor_roles"
        case canvas = "canvas_documents", shortcutKeys = "interaction_shortcut_keys"
    }

    /// The bundled rules.
    static let shared: WebSurfaces = Rules.load("web_surfaces")
    /// The extra blocklist entries.
    static let extra: BlocklistExtra = Rules.load("blocklist")

    /// The kind of an app from its bundle ID and bundle location.
    func kind(bundleID: String, bundleURL: URL?) -> SurfaceKind {
        let id = bundleID.lowercased()
        func listed(_ items: [String]) -> Bool { items.contains { id == $0 || id.hasPrefix($0 + ".") } }
        if listed(unsupported.bundleIDs) { return .unsupported }
        if listed(chromium) { return .chromium }
        if listed(gecko) { return .gecko }
        if let bundleURL, bundleURL.pathExtension == "app",
           FileManager.default.fileExists(atPath: bundleURL.appendingPathComponent(electron.relativePath).path) {
            return .electron
        }
        return .native
    }

    /// True for a Google Docs, Sheets or Slides editor address (spec 7.2).
    func isCanvasDocument(_ url: URL) -> Bool {
        guard url.scheme?.lowercased() == canvas.scheme, url.host?.lowercased() == canvas.host else { return false }
        var path = url.path
        let parts = path.split(separator: "/", omittingEmptySubsequences: false)
        if parts.count > 2, parts[1] == "u", !parts[2].isEmpty, parts[2].allSatisfy(\.isNumber) {
            path = "/" + parts.dropFirst(3).joined(separator: "/")
        }
        return canvas.pathPrefixes.contains { path.hasPrefix($0) }
    }
}
