import Foundation

/// The compiled patterns of credential_patterns.json.
struct CredentialPatterns: Sendable {
    static let shared = CredentialPatterns(Rules.credential)

    let invisible: Rx
    let space: Rx
    let lineBreak: Rx
    let recorderFiles: Rx
    let secretFiles: Rx
    let pathCredentials: Rx
    let titleCredentials: Rx
    let privateKey: Rx
    let privateKeyBlock: Rx
    let userinfo: Rx
    let querySecret: Rx
    let bearer: Rx
    let providerTokens: Rx
    let labelAssignment: Rx
    let fieldBoundary: Rx
    let codeValue: Rx
    let labelOnlyLine: Rx
    let genericToken: Rx
    let percentToken: Rx
    let percentRun: Rx

    /// Compiles every pattern the scan uses.
    init(_ rules: CredentialRules) {
        func exclusion(_ key: String) -> Rx { Rx(rules.exclusion[key]!) }
        func redaction(_ key: String) -> Rx { Rx(rules.redaction[key]!) }
        invisible = Rx(rules.normalize.invisible)
        space = Rx(rules.normalize.space)
        lineBreak = Rx(rules.normalize.lineBreak)
        recorderFiles = exclusion("recorder_files")
        secretFiles = exclusion("secret_files")
        pathCredentials = exclusion("path_credentials")
        titleCredentials = exclusion("title_credentials_titles_only")
        privateKey = exclusion("private_key")
        privateKeyBlock = exclusion("private_key_block")
        userinfo = redaction("userinfo")
        querySecret = redaction("query_secret")
        bearer = redaction("bearer")
        providerTokens = redaction("provider_tokens")
        labelAssignment = redaction("header_label_assignment")
        fieldBoundary = redaction("field_boundary")
        codeValue = Rx(rules.redaction["code_value_not_a_secret"]! + "\\z")
        labelOnlyLine = redaction("label_only_line")
        genericToken = redaction("generic_long_token")
        percentToken = redaction("percent_encoded_token")
        percentRun = redaction("percent_run")
    }
}
