import Foundation

/// A private value found in a line: UTF-16 offsets, kind, code label and normal form.
struct CodeSpan {
    let start: Int
    let end: Int
    let kind: String
    let label: String
    let normal: String
}
