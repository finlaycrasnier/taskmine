// Hide, code, tidy and the last gate (reader spec 0, 5.1, 5.5, 5.6, 6.1;
// docs/ARCHITECTURE.md "Menu-bar app").
//
// Choices the implementation must meet:
// - Reading has a public memberwise init
//   `Reading(title:text:metadata:provenance:kind:)` with String provenance
//   ("native_accessibility" ...) and String kind ("screen_text" | "interaction" | "capture_gap"),
//   the wire values of docs/CONTRACTS.md section 2.
// - prepare() writes the private-data counts to metadata["codes"] as "KIND=n,KIND=n"
//   (kinds sorted, only counts above 0), counted over the saved text and title.
// - A title excluded by the title rules makes prepare() throw (any error).
// - A credential left anywhere after hiding, including a metadata key, throws
//   PipelineError.secretRemains.
import Foundation
import Testing
import TaskmineCore

private let pw = "FAKE-acmeflo-" + "Fixture-pw-42"            // values.pw
private let openai = "sk-proj-" + "FAKEacmefloFixture0123456789" // values.openai

private func reading(
    title: String = "Notes - TextEdit",
    text: String,
    metadata: [String: String] = ["app_bundle_id": "com.apple.TextEdit", "app_name": "TextEdit"]
) -> Reading {
    Reading(title: title, text: text, metadata: metadata,
            provenance: "native_accessibility", kind: "screen_text")
}

private func pipeline() -> Pipeline {
    Pipeline(coder: PrivateDataCoder(keyHex: Fixtures.keyHex))
}

@Suite struct PipelineTests {
    @Test func fixtureValuesMatch() {
        #expect(Fixtures.value("pw") == pw)
        #expect(Fixtures.value("openai") == openai)
    }

    @Test func passwordInTextIsRedacted() throws {
        let out = try pipeline().prepare(reading(text: "export DB_PASSWORD=" + pw + "\nnpm test"))
        #expect(out.text == "export DB_PASSWORD=[redacted]\nnpm test")
        let leaked = out.text.contains(pw)
        #expect(!leaked)
        #expect(!TextSafety.containsCredential(out.text))
    }

    @Test func credentialDocumentTitleThrows() {
        #expect(throws: (any Error).self) {
            _ = try pipeline().prepare(reading(title: ".env.local - Acmeflo - Cursor", text: "DEBUG=1"))
        }
    }

    @Test func privateKeyFileTitleThrows() {
        #expect(throws: (any Error).self) {
            _ = try pipeline().prepare(reading(title: "id_ed25519 — Edited", text: "notes"))
        }
    }

    @Test func excludedTextThrows() {
        let key = "-----BEGIN " + "OPENSSH PRIVATE KEY-----\n" + "FAKEfixtureKeyMaterial0042"
            + "\n-----END " + "OPENSSH PRIVATE KEY-----"
        #expect(throws: (any Error).self) {
            _ = try pipeline().prepare(reading(text: key))
        }
    }

    @Test func ibanIsCodedWithCount() throws {
        let out = try pipeline().prepare(reading(text: "Pay to IT60 X054 2811 1010 0000 0123 456 please"))
        #expect(out.text == "Pay to [IBAN·IT·4154] please")
        #expect(out.metadata["codes"] == "IBAN=1")
    }

    @Test func titleIsCodedAndCounted() throws {
        let out = try pipeline().prepare(reading(
            title: "Payment to mario.rossi@acmecorp.it - Mail",
            text: "Pay to IT60 X054 2811 1010 0000 0123 456 please"))
        #expect(out.title == "Payment to [EMAIL·corp·37c7] - Mail")
        #expect(out.metadata["codes"] == "EMAIL=1,IBAN=1")
    }

    @Test func noCodesNoCountKey() throws {
        let out = try pipeline().prepare(reading(text: "Approval is required before payment."))
        #expect(out.metadata["codes"] == nil)
    }

    @Test func metadataValuesAreHidden() throws {
        var metadata = ["app_bundle_id": "com.apple.TextEdit", "app_name": "TextEdit"]
        metadata["focus_path"] = "API_KEY=" + openai
        let out = try pipeline().prepare(reading(text: "Settings", metadata: metadata))
        let value = try #require(out.metadata["focus_path"])
        let leaked = value.contains(openai)
        #expect(!leaked)
        #expect(value.contains("[redacted]"))
        #expect(out.metadata["app_bundle_id"] == "com.apple.TextEdit")
        #expect(out.metadata["app_name"] == "TextEdit")
    }

    @Test func tidyRunsAfterHide() throws {
        let out = try pipeline().prepare(reading(text: "Inbox\n3:45 PM\nReply\nReply"))
        #expect(out.text == "Inbox\n⟨clock⟩\nReply\n⟨repeat×2⟩")
    }

    @Test func keepsProvenanceAndKind() throws {
        let out = try pipeline().prepare(reading(text: "Approval is required before payment."))
        #expect(out.provenance == "native_accessibility")
        #expect(out.kind == "screen_text")
        #expect(out.title == "Notes - TextEdit")
        #expect(out.text == "Approval is required before payment.")
    }

    @Test func secretInMetadataKeyFailsLastGate() {
        var metadata = ["app_bundle_id": "com.apple.TextEdit"]
        metadata[openai] = "x"
        do {
            _ = try pipeline().prepare(reading(text: "Settings", metadata: metadata))
            Issue.record("a credential in a metadata key passed the last gate")
        } catch {
            guard case PipelineError.secretRemains = error else {
                Issue.record("expected PipelineError.secretRemains, got another error")
                return
            }
        }
    }
}

/// The Reader protocol shape: one Reading type for every reader.
private struct FakeReader: Reader {
    let provenance = "synthetic"
    func start() throws {}
    func stop() {}
    func snapshot(of window: WindowRef) -> Reading? { nil }
}

@Suite struct ReaderProtocolTests {
    @Test func fakeReaderConforms() throws {
        let r: any Reader = FakeReader()
        #expect(r.provenance == "synthetic")
        try r.start()
        r.stop()
    }
}
