import SwiftUI

@main
struct GottaScanApp: App {
    @StateObject private var store = ScanStore()

    var body: some Scene {
        WindowGroup {
            HomeView()
                .environmentObject(store)
        }
    }
}
