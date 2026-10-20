// Exclusion rules: app blocklist, URL blocklist, private windows (reader spec 3.4, 4).
// Oracle: sensitive_surfaces.json, 24 apps, 18 urls, 8 private windows.
// A null expectation means the surface stays eligible.
import Foundation
import Testing
import TaskmineCore

@Suite struct SurfacesTests {
    static let apps = Fixtures.surfaces.apps
    static let urls = Fixtures.surfaces.urls
    static let windows = Numbered.list("window", Fixtures.surfaces.private_windows)

    @Test func counts() {
        #expect(Self.apps.count == 24)
        #expect(Self.urls.count == 18)
        #expect(Self.windows.count == 8)
    }

    @Test(arguments: apps)
    func appReason(_ c: AppCase) {
        #expect(Surfaces.appReason(c.bundle_id) == c.expect, "\(c.bundle_id)")
    }

    @Test(arguments: urls)
    func urlReason(_ c: URLCase) {
        #expect(Surfaces.urlReason(c.url) == c.expect, "\(c.url)")
    }

    @Test(arguments: windows)
    func privateWindow(_ c: Numbered<WindowCase>) {
        let got = Surfaces.isPrivateWindow(title: c.value.title, labels: c.value.labels)
        #expect(got == c.value.expect, "\(c.testDescription): \(c.value.title) | \(c.value.labels)")
    }
}
