import Foundation

/// The capture policy the core serves, taskmine.policy/1.
public struct Policy: Codable, Sendable, Equatable {
    /// The schema name every policy carries.
    public static let schema = "taskmine.policy/1"

    /// Whether the readers run.
    public enum State: String, Codable, Sendable { case active, paused }

    public let version: Int
    public let state: State
    public let allowedApps: [String]
    public let excludedDomains: [String]
    public let allowedProvenance: [String]
    public let retentionDays: Int

    /// A policy.
    public init(version: Int, state: State, allowedApps: [String], excludedDomains: [String],
                allowedProvenance: [String], retentionDays: Int) {
        self.version = version
        self.state = state
        self.allowedApps = allowedApps
        self.excludedDomains = excludedDomains
        self.allowedProvenance = allowedProvenance
        self.retentionDays = retentionDays
    }

    enum CodingKeys: String, CodingKey {
        case schema, version, state, allowedApps = "allowed_apps", excludedDomains = "excluded_domains"
        case allowedProvenance = "allowed_provenance", retentionDays = "retention_days"
    }

    /// Decodes and checks the schema and the retention range.
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        try WireFormat.expect(c, .schema, Self.schema)
        version = try c.decode(Int.self, forKey: .version)
        state = try c.decode(State.self, forKey: .state)
        allowedApps = try c.decode([String].self, forKey: .allowedApps)
        excludedDomains = try c.decode([String].self, forKey: .excludedDomains)
        allowedProvenance = try c.decode([String].self, forKey: .allowedProvenance)
        retentionDays = try c.decode(Int.self, forKey: .retentionDays)
        guard (1...365).contains(retentionDays) else { throw WireFormat.invalid(c, .retentionDays, "outside 1...365") }
    }

    /// Encodes the wire JSON.
    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(Self.schema, forKey: .schema)
        try c.encode(version, forKey: .version)
        try c.encode(state, forKey: .state)
        try c.encode(allowedApps, forKey: .allowedApps)
        try c.encode(excludedDomains, forKey: .excludedDomains)
        try c.encode(allowedProvenance, forKey: .allowedProvenance)
        try c.encode(retentionDays, forKey: .retentionDays)
    }

    /// True when the policy allows an app: `*` allows every app.
    public func allows(app bundleID: String) -> Bool {
        allowedApps.contains("*") || allowedApps.contains { $0.lowercased() == bundleID.lowercased() }
    }

    /// True when a host or one of its parent domains is excluded.
    public func excludes(host: String) -> Bool {
        let value = host.lowercased().trimmingCharacters(in: CharacterSet(charactersIn: "."))
        return excludedDomains.contains { domain in
            let d = domain.lowercased().trimmingCharacters(in: CharacterSet(charactersIn: "."))
            return !d.isEmpty && (value == d || value.hasSuffix("." + d))
        }
    }
}
