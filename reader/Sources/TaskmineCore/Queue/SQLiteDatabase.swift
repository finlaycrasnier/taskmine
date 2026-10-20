import Foundation
import SQLite3

/// A thin wrapper over one SQLite connection: execute, run with bindings, read rows.
final class SQLiteDatabase {
    private var handle: OpaquePointer?
    private static let transient = unsafeBitCast(-1, to: sqlite3_destructor_type.self)

    /// Opens or creates the file at `path`.
    init(path: String) throws {
        let flags = SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE | SQLITE_OPEN_FULLMUTEX
        guard sqlite3_open_v2(path, &handle, flags, nil) == SQLITE_OK else {
            let error = QueueError.sqlite(message: Self.message(handle))
            sqlite3_close_v2(handle)
            throw error
        }
    }

    deinit { sqlite3_close_v2(handle) }

    /// The last error message of a connection.
    private static func message(_ db: OpaquePointer?) -> String {
        db.flatMap { sqlite3_errmsg($0) }.map { String(cString: $0) } ?? "cannot open database"
    }

    /// Runs one or more statements without bindings.
    func execute(_ sql: String) throws {
        guard sqlite3_exec(handle, sql, nil, nil, nil) == SQLITE_OK else {
            throw QueueError.sqlite(message: Self.message(handle))
        }
    }

    /// Runs one statement and returns how many rows it changed.
    @discardableResult
    func run(_ sql: String, _ values: [SQLValue] = []) throws -> Int {
        let stmt = try prepare(sql, values)
        defer { sqlite3_finalize(stmt) }
        guard sqlite3_step(stmt) == SQLITE_DONE else { throw QueueError.sqlite(message: Self.message(handle)) }
        return Int(sqlite3_changes(handle))
    }

    /// Runs one query and returns every row.
    func rows(_ sql: String, _ values: [SQLValue] = []) throws -> [[SQLValue]] {
        let stmt = try prepare(sql, values)
        defer { sqlite3_finalize(stmt) }
        var out: [[SQLValue]] = []
        while true {
            let step = sqlite3_step(stmt)
            if step == SQLITE_DONE { return out }
            guard step == SQLITE_ROW else { throw QueueError.sqlite(message: Self.message(handle)) }
            out.append((0..<sqlite3_column_count(stmt)).map { column(stmt, $0) })
        }
    }

    /// Runs `body` inside one transaction; any error rolls it back.
    func transaction<T>(_ body: () throws -> T) throws -> T {
        try execute("BEGIN IMMEDIATE")
        do {
            let result = try body()
            try execute("COMMIT")
            return result
        } catch {
            try? execute("ROLLBACK")
            throw error
        }
    }

    /// A prepared statement with its values bound.
    private func prepare(_ sql: String, _ values: [SQLValue]) throws -> OpaquePointer? {
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(handle, sql, -1, &stmt, nil) == SQLITE_OK else {
            throw QueueError.sqlite(message: Self.message(handle))
        }
        for (index, value) in values.enumerated() {
            let i = Int32(index + 1)
            switch value {
            case .int(let n): sqlite3_bind_int64(stmt, i, n)
            case .text(let s): sqlite3_bind_text(stmt, i, s, -1, Self.transient)
            case .null: sqlite3_bind_null(stmt, i)
            }
        }
        return stmt
    }

    /// One column of the current row.
    private func column(_ stmt: OpaquePointer?, _ index: Int32) -> SQLValue {
        switch sqlite3_column_type(stmt, index) {
        case SQLITE_INTEGER: return .int(sqlite3_column_int64(stmt, index))
        case SQLITE_NULL: return .null
        default:
            guard let bytes = sqlite3_column_blob(stmt, index) else { return .text("") }
            let count = Int(sqlite3_column_bytes(stmt, index))
            return .text(String(decoding: UnsafeRawBufferPointer(start: bytes, count: count), as: UTF8.self))
        }
    }
}
