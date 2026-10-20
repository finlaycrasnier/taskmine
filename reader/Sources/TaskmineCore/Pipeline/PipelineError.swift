import Foundation

/// Why the pipeline refused a reading. Reasons are fixed codes, never text.
public enum PipelineError: Error, Equatable {
    /// The secret rules exclude the whole reading (private_key, credential_document, ...).
    case excluded(reason: String)
    /// The last gate found a credential after hiding.
    case secretRemains
}
