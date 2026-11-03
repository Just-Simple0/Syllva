//! The Core child process and the stdio protocol client (spec §2.2-§2.4).
//!
//! The Core is started with an allowlisted environment and receives `shell_secret` once,
//! in `hello` on stdin. Its stdout is protocol only: a reader thread passes replies to the
//! single requester (one request at a time), drops `pong`s, and treats any bad line as a
//! protocol violation (the Core is stopped). Its stderr is drained and never forwarded or
//! logged (I9). A ping goes out every 10 s; the Core exits by itself after 30 s without one.

use serde_json::{json, Map, Value};
use std::io::{BufReader, Read, Write};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::mpsc::{channel, Receiver, RecvTimeoutError};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;
use syllva_shell_proto::{env::core_env, frame, secret::ShellSecret};

pub const PLATFORM: &str = if cfg!(target_os = "macos") {
    "darwin"
} else if cfg!(windows) {
    "win32"
} else {
    "linux"
};
const REPLY_TIMEOUT: Duration = Duration::from_secs(90);

pub struct Core {
    stdin: Arc<Mutex<ChildStdin>>,
    replies: Mutex<Receiver<Map<String, Value>>>,
    child: Arc<Mutex<Child>>,
    stopping: Arc<AtomicBool>,
    pub port: u16,
}

fn core_command() -> Command {
    // Development: SYLLVA_CORE_BIN points at the `syllva` executable; the bundled sidecar replaces this (R2-5).
    let program = std::env::var("SYLLVA_CORE_BIN").unwrap_or_else(|_| "syllva".into());
    let mut command = Command::new(program);
    command
        .args(["core", "--stdio"])
        .env_clear()
        .envs(core_env(std::env::vars()))
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    command
}

fn write_message(stdin: &Mutex<ChildStdin>, message: Map<String, Value>) -> Result<(), String> {
    let line = frame::encode_message(message).map_err(|e| format!("{e:?}"))?;
    let mut stdin = stdin.lock().map_err(|_| "stdin lock poisoned")?;
    stdin.write_all(&line).and_then(|_| stdin.flush()).map_err(|_| "core is gone".to_string())
}

fn object(value: Value) -> Map<String, Value> {
    match value {
        Value::Object(map) => map,
        _ => Map::new(),
    }
}

impl Core {
    /// Spawn the Core, send `hello`, wait for `ready`. Returns the Core and the first bootstrap token.
    pub fn start(secret: &ShellSecret) -> Result<(Core, String), String> {
        let mut child = core_command().spawn().map_err(|e| format!("cannot start the Core: {}", e.kind()))?;
        let stdin = Arc::new(Mutex::new(child.stdin.take().ok_or("no stdin")?));
        let stdout = child.stdout.take().ok_or("no stdout")?;
        let mut stderr = child.stderr.take().ok_or("no stderr")?;
        let child = Arc::new(Mutex::new(child));
        let watched = Arc::clone(&child);
        let stopping = Arc::new(AtomicBool::new(false));
        let watch_stop = Arc::clone(&stopping);
        thread::spawn(move || {
            let mut sink = [0u8; 4096];
            while matches!(stderr.read(&mut sink), Ok(n) if n > 0) {} // drained, never forwarded (I9)
        });
        let (tx, rx) = channel();
        thread::spawn(move || {
            let mut reader = BufReader::new(stdout);
            loop {
                match frame::read_message(&mut reader) {
                    Ok(message) if message.get("t").and_then(Value::as_str) == Some("pong") => continue,
                    Ok(message) => {
                        if tx.send(message).is_err() {
                            return;
                        }
                    }
                    Err(_) if watch_stop.load(Ordering::SeqCst) => return, // the app is quitting: expected
                    Err(error) => {
                        // A protocol violation stops the Core (plan rev 2, item 8); a Core that exited or broke the
                        // protocol leaves nothing to relay to, so the app ends too (its session jar dies with it).
                        if let Ok(mut child) = watched.lock() {
                            let _ = child.kill();
                        }
                        eprintln!("syllva: the Core stopped ({})", if error == frame::FrameError::Closed { "exited" } else { "protocol violation" });
                        std::process::exit(2);
                    }
                }
            }
        });
        write_message(
            &stdin,
            object(json!({"t": "hello", "secret_b64": secret.hello_value(), "shell_version": env!("CARGO_PKG_VERSION"), "platform": PLATFORM})),
        )?;
        let ready = rx.recv_timeout(Duration::from_secs(60)).map_err(|_| "the Core did not answer hello")?;
        if ready.get("t").and_then(Value::as_str) != Some("ready") {
            let code = ready.get("code").and_then(Value::as_str).unwrap_or("UNKNOWN").to_string();
            if let Ok(mut child) = child.lock() {
                let _ = child.kill();
            }
            return Err(format!("the Core refused to start: {code}"));
        }
        let port = ready.get("port").and_then(Value::as_u64).filter(|p| *p > 0 && *p < 65536).ok_or("bad port")? as u16;
        let bootstrap = ready.get("bootstrap").and_then(Value::as_str).ok_or("no bootstrap")?.to_string();
        let pinger = Arc::clone(&stdin);
        thread::spawn(move || {
            let mut seq: u64 = 0;
            loop {
                thread::sleep(Duration::from_secs(10));
                seq += 1;
                if write_message(&pinger, object(json!({"t": "ping", "seq": seq}))).is_err() {
                    return;
                }
            }
        });
        Ok((Core { stdin, replies: Mutex::new(rx), child, stopping, port }, bootstrap))
    }

    /// One request, one reply (requests are serialized by the replies lock).
    pub fn request(&self, message: Map<String, Value>) -> Result<Map<String, Value>, String> {
        let replies = self.replies.lock().map_err(|_| "reply lock poisoned")?;
        write_message(&self.stdin, message)?;
        match replies.recv_timeout(REPLY_TIMEOUT) {
            Ok(reply) => Ok(reply),
            Err(RecvTimeoutError::Timeout) => Err("the Core did not answer".into()),
            Err(RecvTimeoutError::Disconnected) => Err("the Core is gone".into()),
        }
    }

    pub fn shutdown(&self) {
        self.stopping.store(true, Ordering::SeqCst);
        let _ = write_message(&self.stdin, object(json!({"t": "shutdown", "grace_ms": 3000})));
        if let Ok(mut child) = self.child.lock() {
            for _ in 0..50 {
                if matches!(child.try_wait(), Ok(Some(_))) {
                    return;
                }
                thread::sleep(Duration::from_millis(100));
            }
            let _ = child.kill();
        }
    }
}
