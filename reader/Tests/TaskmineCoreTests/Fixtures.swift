// Fixture loading shared by the suites. The files under Fixtures/ mirror data/fixtures.
// Every case type prints its id only, so a failure names the case, never a value
// (credential_cases.json: "Report failures by case id, never by value").
import Foundation
import Testing

enum Fixtures {
    static let directory: URL = Bundle.module.resourceURL!.appendingPathComponent("Fixtures")

    static func data(_ name: String) -> Data {
        let url = directory.appendingPathComponent(name)
        do { return try Data(contentsOf: url) } catch { fatalError("missing fixture \(name): \(error)") }
    }

    static func decode<T: Decodable>(_ type: T.Type, _ name: String) -> T {
        do { return try JSONDecoder().decode(T.self, from: data(name)) } catch {
            fatalError("cannot decode fixture \(name): \(error)")
        }
    }

    static let credential = decode(CredentialFile.self, "credential_cases.json")
    static let golden = decode(GoldenFile.self, "sanitizer_golden.json")
    static let normalization = decode(NormalizationFile.self, "capture_normalization.json")
    static let surfaces = decode(SurfacesFile.self, "sensitive_surfaces.json")

    /// The coding key of the private-data cases (64 hex digits).
    static var keyHex: String { credential.private_data.key_hex }

    /// The joined value of a named entry in credential_cases.json `values`.
    static func value(_ name: String) -> String {
        guard let parts = credential.values[name] else { fatalError("no value named \(name)") }
        return parts.joined()
    }

    /// Joins `parts`. A part is a value reference only when the text after "$" names a
    /// value; literal parts such as "$ deploy\nPassword: " start with "$" and stay as typed.
    static func build(_ parts: [String]) -> String {
        parts.map { part in
            if part.hasPrefix("$"), credential.values[String(part.dropFirst())] != nil {
                return value(String(part.dropFirst()))
            }
            return part
        }.joined()
    }
}

// MARK: credential_cases.json

struct CredentialFile: Decodable, Sendable {
    let sanitizer_version: Int
    let values: [String: [String]]
    let cases: [CredentialCase]
    let private_data: PrivateDataFile
}

struct CredentialCase: Decodable, Sendable, CustomTestStringConvertible {
    let id: String
    let kind: String          // text | title | url
    let parts: [String]
    let expect: String        // redact | exclude | keep | drop | url
    let secret: String?       // name of a `values` entry that must never survive
    let reason: String?
    let keep: [String]?
    let url: String?
    let limit: Int?
    let absent_prefix: Int?
    var testDescription: String { id }
}

struct PrivateDataFile: Decodable, Sendable {
    let key_hex: String
    let cases: [PrivateDataCase]
}

struct PrivateDataCase: Decodable, Sendable, CustomTestStringConvertible {
    let id: String
    let parts: [String]
    let coded: String
    let counts: [String: Int]
    let keyless: Bool?
    var testDescription: String { id }
}

// MARK: sanitizer_golden.json

struct GoldenFile: Decodable, Sendable {
    let sanitizer_version: Int
    let min_supported_version: Int
    let cases: [GoldenCase]
}

struct GoldenCase: Decodable, Sendable, CustomTestStringConvertible {
    let id: String
    let kind: String
    let input_parts: [String]   // joined as is; no "$" references in this file
    let output: String?         // null: excluded (text, title) or dropped (url)
    var testDescription: String { id }
}

// MARK: capture_normalization.json

struct NormalizationFile: Decodable, Sendable {
    let texts: [StringCase]
    let urls: [StringCase]
    let clock_lines: [BoolCase]
}

struct StringCase: Decodable, Sendable {
    let input: String
    let expect: String
}

struct BoolCase: Decodable, Sendable {
    let input: String
    let expect: Bool
}

/// A fixture case without an id, named by its position in the file.
struct Numbered<Case: Sendable>: Sendable, CustomTestStringConvertible {
    let index: Int
    let prefix: String
    let value: Case
    var testDescription: String { "\(prefix)\(index)" }

    static func list(_ prefix: String, _ cases: [Case]) -> [Numbered<Case>] {
        cases.enumerated().map { Numbered(index: $0.offset, prefix: prefix, value: $0.element) }
    }
}

// MARK: sensitive_surfaces.json

struct SurfacesFile: Decodable, Sendable {
    let apps: [AppCase]
    let urls: [URLCase]
    let private_windows: [WindowCase]
}

struct AppCase: Decodable, Sendable, CustomTestStringConvertible {
    let bundle_id: String
    let expect: String?
    var testDescription: String { bundle_id }
}

struct URLCase: Decodable, Sendable, CustomTestStringConvertible {
    let url: String
    let expect: String?
    var testDescription: String { url }
}

struct WindowCase: Decodable, Sendable {
    let title: String
    let labels: [String]
    let expect: Bool
}

// MARK: helpers

extension String {
    /// Number of Unicode scalars, the unit of every limit in the reader spec.
    var scalarCount: Int { unicodeScalars.count }
}
