import Foundation

/// A window whose accessible text was thin, held for the picture reader until the next cycle (spec 7.2).
struct OCRCandidate {
    let surface: Surface
    /// `canvas_document` or `interface_text`.
    let reason: String
    /// SHA-256 of the accessible text, for the 30 s unchanged-text limit.
    let textHash: String
    let at: Date
}
