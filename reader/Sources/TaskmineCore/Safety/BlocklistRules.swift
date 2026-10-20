import Foundation

/// data/rules/blocklist.json.
struct BlocklistRules: Decodable, Sendable {
    struct HostLabels: Decodable, Sendable {
        let signInPage: [String]
        let sensitiveDomain: [String]

        enum CodingKeys: String, CodingKey {
            case signInPage = "sign_in_page", sensitiveDomain = "sensitive_domain"
        }
    }

    struct PrivateWindow: Decodable, Sendable {
        let titleContains: [String]
        let titleSuffixes: [String]
        let labelsExact: [String]
        let labelContains: [String]

        enum CodingKeys: String, CodingKey {
            case titleContains = "title_contains", titleSuffixes = "title_suffixes"
            case labelsExact = "toolbar_labels_exact", labelContains = "toolbar_label_contains"
        }
    }

    let apps: [String: [String]]
    let hosts: [String: [String]]
    let sensitiveTLDs: [String]
    let hostLabels: HostLabels
    let signInSegments: [String]
    let privateWindow: PrivateWindow

    enum CodingKeys: String, CodingKey {
        case apps, hosts, sensitiveTLDs = "sensitive_top_level_domains", hostLabels = "host_label_rules"
        case signInSegments = "sign_in_path_segments", privateWindow = "private_window"
    }
}
