import Foundation

/// One interaction ready for the pipeline; an edit burst report carries its burst.
struct InteractionRecord {
    /// Kind `interaction`, provenance `native_interaction`; text empty except a final field value.
    let reading: Reading
    /// The edit burst report, or nil for a click, focus, scroll or shortcut.
    let burst: BurstEvent?
    let at: Date
}
