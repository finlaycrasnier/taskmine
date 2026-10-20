import Foundation

/// What every reader returns: one window's title, text and metadata, before or after the pipeline.
public struct Reading: Sendable, Equatable {
    public var title: String
    public var text: String
    public var metadata: [String: String]
    /// Wire provenance: native_accessibility, native_interaction, screen_ocr, app_reader or synthetic.
    public var provenance: String
    /// Wire content type: screen_text, interaction or capture_gap.
    public var kind: String

    /// A reading.
    public init(title: String, text: String, metadata: [String: String], provenance: String, kind: String) {
        self.title = title
        self.text = text
        self.metadata = metadata
        self.provenance = provenance
        self.kind = kind
    }
}
