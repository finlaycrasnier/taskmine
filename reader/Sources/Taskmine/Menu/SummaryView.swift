import AppKit

/// The summary block at the top of the menu: a bold title and wrapped detail text, 320 points wide.
final class SummaryView: NSView {
    private static let width: CGFloat = 320
    private let titleLabel = NSTextField(labelWithString: "")
    private let detailLabel = NSTextField(wrappingLabelWithString: "")

    /// An empty summary.
    init() {
        super.init(frame: NSRect(x: 0, y: 0, width: Self.width, height: 60))
        titleLabel.font = .boldSystemFont(ofSize: NSFont.systemFontSize)
        detailLabel.font = .systemFont(ofSize: NSFont.smallSystemFontSize)
        detailLabel.textColor = .secondaryLabelColor
        detailLabel.preferredMaxLayoutWidth = Self.width - 28
        for label in [titleLabel, detailLabel] {
            label.translatesAutoresizingMaskIntoConstraints = false
            addSubview(label)
        }
        NSLayoutConstraint.activate([
            titleLabel.topAnchor.constraint(equalTo: topAnchor, constant: 6),
            titleLabel.leadingAnchor.constraint(equalTo: leadingAnchor, constant: 14),
            titleLabel.trailingAnchor.constraint(lessThanOrEqualTo: trailingAnchor, constant: -14),
            detailLabel.topAnchor.constraint(equalTo: titleLabel.bottomAnchor, constant: 3),
            detailLabel.leadingAnchor.constraint(equalTo: leadingAnchor, constant: 14),
            detailLabel.trailingAnchor.constraint(equalTo: trailingAnchor, constant: -14),
            detailLabel.bottomAnchor.constraint(equalTo: bottomAnchor, constant: -6),
            widthAnchor.constraint(equalToConstant: Self.width),
        ])
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { nil }

    /// Shows a title and detail and resizes to fit.
    func show(title: String, detail: String) {
        titleLabel.stringValue = title
        detailLabel.stringValue = detail
        layoutSubtreeIfNeeded()
        setFrameSize(fittingSize)
    }
}
