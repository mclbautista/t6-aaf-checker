import AppKit
import SwiftUI
import UniformTypeIdentifiers
import AAFGateCore

@main
struct AAFGateApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @StateObject private var model = GateModel.shared

    var body: some Scene {
        Window("AAF Gate", id: "main") {
            GateView(model: model)
                .onOpenURL { model.enqueue([$0]) }
        }
        .defaultSize(width: 600, height: 420)
        .commands {
            CommandGroup(replacing: .newItem) {
                Button("Open…") { model.openFiles() }
                    .keyboardShortcut("o")
            }
        }
    }
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    func application(_ application: NSApplication, open urls: [URL]) {
        GateModel.shared.enqueue(urls)
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows: Bool) -> Bool {
        sender.windows.first { $0.canBecomeMain }?.makeKeyAndOrderFront(nil)
        return true
    }
}

@MainActor
final class GateModel: ObservableObject {
    static let shared = GateModel()

    @Published private(set) var pending: [URL] = []
    @Published private(set) var isRunning = false
    @Published private(set) var currentFile: URL?
    @Published private(set) var verdict: Verdict?
    @Published private(set) var status = "Drop an AAF to check"
    @Published private(set) var detail = "Or choose File > Open."
    @Published private(set) var progress = ""
    @Published private(set) var reportPaths: [String] = []
    @Published private(set) var completed: [CompletedCheck] = []

    func openFiles() {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [UTType(filenameExtension: "aaf") ?? .data]
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = true
        panel.begin { response in
            if response == .OK { self.enqueue(panel.urls) }
        }
    }

    func enqueue(_ urls: [URL]) {
        for url in urls.map(\.standardizedFileURL) {
            guard url.isFileURL, url.pathExtension.lowercased() == "aaf" else { continue }
            guard !pending.contains(url), !(isRunning && currentFile == url) else { continue }
            pending.append(url)
        }
        NSApp.activate(ignoringOtherApps: true)
        NSApp.windows.first { $0.canBecomeMain }?.makeKeyAndOrderFront(nil)
        guard !isRunning, !pending.isEmpty else { return }
        isRunning = true
        Task { await drainQueue() }
    }

    func acceptDrop(_ providers: [NSItemProvider]) -> Bool {
        let files = providers.filter { $0.hasItemConformingToTypeIdentifier(UTType.fileURL.identifier) }
        guard !files.isEmpty else { return false }
        Task {
            var urls: [URL] = []
            for provider in files {
                let data: Data? = await withCheckedContinuation { continuation in
                    provider.loadDataRepresentation(forTypeIdentifier: UTType.fileURL.identifier) { data, _ in
                        continuation.resume(returning: data)
                    }
                }
                if let data, let url = URL(dataRepresentation: data, relativeTo: nil) { urls.append(url) }
            }
            enqueue(urls)
        }
        return true
    }

    private func drainQueue() async {
        while !pending.isEmpty {
            let aaf = pending.removeFirst()
            currentFile = aaf
            verdict = nil
            status = "Checking \(aaf.lastPathComponent)"
            detail = ""
            progress = "Starting…"
            reportPaths = []
            let executable = Bundle.main.bundleURL
                .appendingPathComponent("Contents/Helpers/aaf-qc-worker/aaf-qc-worker")
            do {
                let result = try await WorkerRunner(executableURL: executable).check(aaf) { event in
                    await self.receive(event)
                }
                verdict = result.verdict
                detail = [result.reason, result.reportError].compactMap { $0 }.joined(separator: "\n")
                reportPaths = result.reportPaths
            } catch {
                verdict = .error
                detail = error.localizedDescription
            }
            progress = ""
            status = aaf.lastPathComponent
            completed.append(CompletedCheck(file: aaf.lastPathComponent, verdict: verdict ?? .error))
        }
        isRunning = false
    }

    private func receive(_ event: WorkerEvent) {
        if case .progress(let done, let total, let bytes) = event {
            progress = "\(done) of \(total) clips · \(ByteCountFormatter.string(fromByteCount: bytes, countStyle: .file)) read"
        }
    }

    func revealReports() {
        NSWorkspace.shared.activateFileViewerSelecting(reportPaths.map { URL(fileURLWithPath: $0) })
    }
}

struct CompletedCheck: Identifiable {
    let id = UUID()
    let file: String
    let verdict: Verdict
}

struct GateView: View {
    @ObservedObject var model: GateModel
    @State private var isTargeted = false

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack {
                Image(systemName: "waveform.badge.magnifyingglass")
                Text("AAF Gate").font(.title2.bold())
                Spacer()
                Button("Open AAF…") { model.openFiles() }
            }
            VStack(alignment: .leading, spacing: 10) {
                if let verdict = model.verdict {
                    Text(verdict.rawValue)
                        .font(.system(size: 42, weight: .bold, design: .rounded))
                        .foregroundStyle(color(for: verdict))
                        .accessibilityIdentifier("verdict")
                } else if model.isRunning {
                    ProgressView().controlSize(.small)
                } else {
                    Image(systemName: "square.and.arrow.down").font(.largeTitle)
                }
                Text(model.status).font(.headline).textSelection(.enabled)
                if !model.detail.isEmpty { Text(model.detail).textSelection(.enabled) }
                if !model.progress.isEmpty { Text(model.progress).foregroundStyle(.secondary) }
                Text("Drop AAF files here").foregroundStyle(.secondary)
            }
            .padding(24)
            .frame(maxWidth: .infinity, minHeight: 180, alignment: .leading)
            .background(isTargeted ? Color.accentColor.opacity(0.15) : Color.secondary.opacity(0.08))
            .clipShape(RoundedRectangle(cornerRadius: 12))
            if !model.reportPaths.isEmpty {
                Button("Reveal reports in Finder") { model.revealReports() }
            }
            if !model.pending.isEmpty {
                Text("Queued: \(model.pending.map(\.lastPathComponent).joined(separator: ", "))")
                    .foregroundStyle(.secondary)
            }
            ScrollView {
                VStack(alignment: .leading, spacing: 8) {
                    ForEach(model.completed) { result in
                        HStack {
                            Text(result.verdict.rawValue).bold().foregroundStyle(color(for: result.verdict))
                            Text(result.file)
                        }
                    }
                }.frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .padding(24)
        .frame(minWidth: 480, minHeight: 360)
        .onDrop(of: [UTType.fileURL], isTargeted: $isTargeted) { model.acceptDrop($0) }
    }

    private func color(for verdict: Verdict) -> Color {
        switch verdict {
        case .pass: .green
        case .fail: .red
        case .error: .orange
        }
    }
}
