import Foundation

/// The rule files under Rules/ (copies of data/rules), decoded once from the bundle.
enum Rules {
    static let credential: CredentialRules = load("credential_patterns")
    static let normalization: NormalizationRules = load("normalization_rules")
    static let blocklist: BlocklistRules = load("blocklist")
    static let privateData: PrivateDataRules = load("private_data_tables")

    /// Decodes one bundled rule file; a missing or broken file is a build error.
    static func load<T: Decodable>(_ name: String) -> T {
        guard let url = Bundle.module.url(forResource: name, withExtension: "json", subdirectory: "Rules")
            ?? Bundle.module.url(forResource: name, withExtension: "json") else {
            fatalError("rule file \(name).json is not in the bundle")
        }
        do { return try JSONDecoder().decode(T.self, from: Data(contentsOf: url)) } catch {
            fatalError("rule file \(name).json does not decode: \(error)")
        }
    }
}
