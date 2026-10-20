import Foundation

/// The outcome of the surface checks: read it, record a withheld gap, or skip with a coverage reason.
enum GateResult {
    case pass(Surface)
    /// A withheld category of spec 4.3.
    case withheld(String)
    /// Not withheld time; the reason feeds coverage when present.
    case skip(CoverageReason?)
}
