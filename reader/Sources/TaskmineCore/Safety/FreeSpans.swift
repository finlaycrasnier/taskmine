import Foundation

/// Free-text detection of private values: IBAN, card, email, tax IDs, phones (reader spec 5.6).
extension CodingPass {
    /// Every value free-text detection finds in a line, overlaps included.
    func freeSpans(_ line: NSString) -> [CodeSpan] {
        let rx = PrivateDataPatterns.shared
        var spans = ibans(line) + cards(line)
        for m in rx["email"].all(line) {
            let address = m.value.lowercased()
            spans.append(CodeSpan(start: m.start, end: m.end, kind: "EMAIL", label: ValueChecks.emailType(address), normal: address))
        }
        for m in rx["codiceFiscale"].all(line) where ValueChecks.codiceFiscaleValid(m.value) {
            spans.append(CodeSpan(start: m.start, end: m.end, kind: "TAXID", label: "IT", normal: m.value.uppercased()))
        }
        for m in rx["partitaIVA"].all(line) where ValueChecks.luhn(m.group(1) ?? "") {
            spans.append(CodeSpan(start: m.start, end: m.end, kind: "TAXID", label: "IT", normal: m.group(1) ?? ""))
        }
        for m in rx["phoneInternational"].all(line) {
            let digits = PyText.digits(m.value)
            guard (8...15).contains(digits.count) else { continue }
            spans.append(CodeSpan(start: m.start, end: m.end, kind: "PHONE", label: ValueChecks.callingCountry(digits), normal: digits))
        }
        for m in rx["phoneItalianMobile"].all(line) {
            spans.append(CodeSpan(start: m.start, end: m.end, kind: "PHONE", label: "IT", normal: "39" + PyText.digits(m.value)))
        }
        return spans
    }

    /// Every checked IBAN in a line, left to right.
    func ibans(_ line: NSString) -> [CodeSpan] {
        var spans: [CodeSpan] = []
        for head in PrivateDataPatterns.shared.ibanHead.all(line) {
            if let last = spans.last, head.start < last.end { continue }
            if let span = iban(at: head.start, in: line) { spans.append(span) }
        }
        return spans
    }

    /// A checked IBAN starting at `start`, spaces or hyphens allowed inside.
    private func iban(at start: Int, in line: NSString) -> CodeSpan? {
        let country = PyText.slice(line, start, start + 2).uppercased()
        guard let size = Rules.privateData.ibanLengths[country],
              let m = PrivateDataPatterns.shared.ibanBodies[size]?.match(line, at: start) else { return nil }
        let iban = PyText.alnum(m.value).uppercased()
        guard ValueChecks.mod97(iban) == 1 else { return nil }
        return CodeSpan(start: start, end: m.end, kind: "IBAN", label: String(iban.prefix(2)), normal: iban)
    }

    /// Every card number in a line, left to right.
    func cards(_ line: NSString) -> [CodeSpan] {
        var spans: [CodeSpan] = []
        for head in PrivateDataPatterns.shared.cardHead.all(line) {
            if let last = spans.last, head.start < last.end { continue }
            if let span = card(at: head.start, in: line) { spans.append(span) }
        }
        return spans
    }

    /// The longest branded, Luhn-valid card number of 13 to 19 digits at `start`.
    private func card(at start: Int, in line: NSString) -> CodeSpan? {
        guard let group = PrivateDataPatterns.shared.cardDigits.match(line, at: start) else { return nil }
        let positions = (start..<group.end).filter { isDigit(line.character(at: $0)) }
        for count in stride(from: positions.count, through: 13, by: -1) {
            let end = positions[count - 1] + 1
            let following = end < line.length ? line.character(at: end) : nil
            if let f = following, isDigit(f) || isLetter(f) || f == 95 { continue }
            if let f = following, f == 46 || f == 44, end + 1 < line.length, isDigit(line.character(at: end + 1)) { continue }
            let number = String(utf16CodeUnits: positions[..<count].map { line.character(at: $0) }, count: count)
            if let brand = ValueChecks.cardBrand(number), ValueChecks.luhn(number) {
                return CodeSpan(start: start, end: end, kind: "CARD", label: brand, normal: number)
            }
        }
        return nil
    }

    /// True for an ASCII digit unit.
    private func isDigit(_ unit: unichar) -> Bool { unit >= 48 && unit <= 57 }

    /// True for an ASCII letter unit.
    private func isLetter(_ unit: unichar) -> Bool { (unit >= 65 && unit <= 90) || (unit >= 97 && unit <= 122) }
}
