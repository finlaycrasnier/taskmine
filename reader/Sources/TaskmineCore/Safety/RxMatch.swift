import Foundation

/// One regex match: UTF-16 offsets into the searched text and its groups.
struct RxMatch {
    let result: NSTextCheckingResult
    let text: NSString

    /// Offset of the first matched unit.
    var start: Int { result.range.location }

    /// Offset after the last matched unit.
    var end: Int { result.range.location + result.range.length }

    /// The whole match.
    var value: String { text.substring(with: result.range) }

    /// A capture group, or nil when it took no part in the match.
    func group(_ index: Int) -> String? {
        let range = result.range(at: index)
        return range.location == NSNotFound ? nil : text.substring(with: range)
    }
}
