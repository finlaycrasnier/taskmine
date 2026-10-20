import Foundation

/// The blocklist entries the safety rules do not decode: denied apps and the editor renderer exception.
struct BlocklistExtra: Decodable, Sendable {
    /// Apps never inspected.
    struct Denied: Decodable, Sendable {
        let exact: [String]

        enum CodingKeys: String, CodingKey { case exact = "exact_lowercase" }
    }

    /// Editors whose bundled renderer web area is allowed.
    struct EditorException: Decodable, Sendable {
        let bundleIDs: [String]

        enum CodingKeys: String, CodingKey { case bundleIDs = "bundle_ids_lowercase" }
    }

    let denied: Denied
    let editor: EditorException

    enum CodingKeys: String, CodingKey { case denied = "apps_denied_other", editor = "editor_renderer_exception" }

    /// True for an app that is never inspected (`com.openai.codex`).
    func isDenied(_ bundleID: String) -> Bool { denied.exact.contains(bundleID.lowercased()) }

    /// True when a web area URL is the editor's own renderer page (blocklist editor_renderer_exception).
    func allowsEditorPage(_ url: URL?, bundleID: String, appPath: String?) -> Bool {
        guard let url, let appPath, editor.bundleIDs.contains(bundleID.lowercased()),
              url.user == nil, url.password == nil, url.query == nil else { return false }
        let host = url.host ?? ""
        let fileOK = url.scheme == "file" && (host.isEmpty || host == "localhost")
        let vscodeOK = url.scheme == "vscode-file" && host == "vscode-app"
        guard fileOK || vscodeOK else { return false }
        let path = url.path
        return path.hasPrefix(appPath + "/Contents/Resources/app/out/") && path.hasSuffix("/workbench.html")
    }
}
