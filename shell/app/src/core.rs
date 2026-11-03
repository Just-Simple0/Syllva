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
use std::sync::atomic::{AtomicU8, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;
use syllva_shell_proto::{env::core_env, frame, secret::ShellSecret, sidecar};

pub const PLATFORM: &str = if cfg!(target_os = "macos") {
    "darwin"
} else if cfg!(windows) {
    "win32"
} else {
    "linux"
};
const REPLY_TIMEOUT: Duration = Duration::from_secs(90);

// Life of the Core as the reader thread sees it (review of PR #52: a refusal at hello must reach the dialog,
// and a Core that dies right after `ready` must still end the app).
const STARTING: u8 = 0;
const LOST_EARLY: u8 = 1; // stdout ended or broke before `start` accepted `ready`: `start` reports it
const RUNNING: u8 = 2; // from here on a lost Core is fatal (`on_lost`)
const STOPPING: u8 = 3; // the app is quitting: a closed stdout is expected

/// Called once when a running Core exits or breaks the protocol (after the Core was killed).
pub type OnLost = Box<dyn Fn(&'static str) + Send + 'static>;

/// What the app does when the Core is lost: nothing is left to relay to, so it ends (its session jar dies with it).
pub fn exit_app(why: &'static str) {
    eprintln!("syllva: the Core stopped ({why})");
    std::process::exit(2);
}

pub struct Core {
    stdin: Arc<Mutex<ChildStdin>>,
    replies: Mutex<Receiver<Map<String, Value>>>,
    child: Arc<Mutex<Child>>,
    state: Arc<AtomicU8>,
    pub port: u16,
}

/// The Core launch: the bundled sidecar (or, in debug builds, `SYLLVA_CORE_BIN`) with the allowlisted environment.
pub fn core_command() -> Result<Command, String> {
    let exe = std::env::current_exe().ok();
    let program = sidecar::core_program(std::env::var("SYLLVA_CORE_BIN").ok(), cfg!(debug_assertions), exe.as_deref())
        .ok_or("cannot locate the Core")?;
    let mut command = Command::new(program);
    command
        .args(["core", "--stdio"])
        .env_clear()
        .envs(core_env(std::env::vars()))
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    Ok(command)
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
        Core::start_with(core_command()?, secret, Box::new(exit_app))
    }

    /// `start` with an explicit launch command and lost-Core handler (tests drive a fake Core this way).
    pub fn start_with(mut command: Command, secret: &ShellSecret, on_lost: OnLost) -> Result<(Core, String), String> {
        command.stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::piped());
        let mut child = command.spawn().map_err(|e| format!("cannot start the Core: {}", e.kind()))?;
        let stdin = Arc::new(Mutex::new(child.stdin.take().ok_or("no stdin")?));
        let stdout = child.stdout.take().ok_or("no stdout")?;
        let mut stderr = child.stderr.take().ok_or("no stderr")?;
        let child = Arc::new(Mutex::new(child));
        let watched = Arc::clone(&child);
        let state = Arc::new(AtomicU8::new(STARTING));
        let watch_state = Arc::clone(&state);
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
                    Err(error) => {
                        // A protocol violation stops the Core (plan rev 2, item 8).
                        if let Ok(mut child) = watched.lock() {
                            let _ = child.kill();
                        }
                        // still starting: `start` sees the dropped channel (or the error frame) and reports it
                        if watch_state.compare_exchange(STARTING, LOST_EARLY, Ordering::SeqCst, Ordering::SeqCst).is_ok() {
                            return;
                        }
                        if watch_state.load(Ordering::SeqCst) == RUNNING {
                            on_lost(if error == frame::FrameError::Closed { "exited" } else { "protocol violation" });
                        }
                        return; // STOPPING: the app is quitting
                    }
                }
            }
        });
        let hello = object(json!({
            "t": "hello", "secret_b64": secret.hello_value(), "shell_version": env!("CARGO_PKG_VERSION"), "platform": PLATFORM,
        }));
        let accepted = write_message(&stdin, hello).and_then(|()| {
            let ready = rx.recv_timeout(Duration::from_secs(60)).map_err(|_| "the Core did not answer hello".to_string())?;
            if ready.get("t").and_then(Value::as_str) != Some("ready") {
                let code = ready.get("code").and_then(Value::as_str).unwrap_or("UNKNOWN");
                return Err(format!("the Core refused to start: {code}"));
            }
            let port = ready.get("port").and_then(Value::as_u64).filter(|p| *p > 0 && *p < 65536).ok_or("bad port")?;
            let bootstrap = ready.get("bootstrap").and_then(Value::as_str).ok_or("no bootstrap")?.to_string();
            // fails if the Core sent `ready` and then died before we accepted it
            state.compare_exchange(STARTING, RUNNING, Ordering::SeqCst, Ordering::SeqCst)
                .map_err(|_| "the Core stopped while starting".to_string())?;
            Ok((port as u16, bootstrap))
        });
        let (port, bootstrap) = match accepted {
            Ok(started) => started,
            Err(message) => {
                state.store(STOPPING, Ordering::SeqCst); // a failed start is reported here, never through on_lost
                if let Ok(mut child) = child.lock() {
                    let _ = child.kill();
                    let _ = child.wait();
                }
                return Err(message);
            }
        };
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
        Ok((Core { stdin, replies: Mutex::new(rx), child, state, port }, bootstrap))
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
        self.state.store(STOPPING, Ordering::SeqCst);
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

#[cfg(test)]
mod tests {
    //! A fake Core (a short Python script) drives the start and watchdog paths (review of PR #52).

    use super::*;
    use std::sync::mpsc::Sender;

    fn python() -> Command {
        let program = std::env::var("PYTHON").unwrap_or_else(|_| if cfg!(windows) { "python".into() } else { "python3".into() });
        Command::new(program)
    }

    fn fake(script: &str) -> Command {
        let mut command = python();
        command.args(["-c", &format!("import sys, time\nsys.stdin.readline()\n{script}")]);
        command
    }

    const READY: &str = "sys.stdout.write('{\"v\":1,\"t\":\"ready\",\"port\":4242,\"bootstrap\":\"b\"}\\n'); sys.stdout.flush()";

    fn lost_channel() -> (OnLost, Receiver<&'static str>) {
        let (tx, rx): (Sender<&'static str>, Receiver<&'static str>) = channel();
        let tx = Mutex::new(tx);
        (Box::new(move |why| { let _ = tx.lock().map(|t| t.send(why)); }), rx)
    }

    #[test]
    fn a_refusal_at_hello_is_returned_not_fatal() {
        let (on_lost, lost) = lost_channel();
        let script = "sys.stdout.write('{\"v\":1,\"t\":\"error\",\"code\":\"DB_LOCKED\"}\\n'); sys.stdout.flush(); sys.exit(3)";
        let error = Core::start_with(fake(script), &ShellSecret::generate(), on_lost).err().expect("refused");
        assert_eq!(error, "the Core refused to start: DB_LOCKED");
        assert!(lost.recv_timeout(Duration::from_millis(500)).is_err());
    }

    #[test]
    fn a_core_that_exits_before_answering_is_reported_by_start() {
        let (on_lost, lost) = lost_channel();
        let error = Core::start_with(fake("sys.exit(1)"), &ShellSecret::generate(), on_lost).err().expect("failed");
        assert_eq!(error, "the Core did not answer hello"); // channel dropped: no 60 s wait
        assert!(lost.recv_timeout(Duration::from_millis(500)).is_err());
    }

    #[test]
    fn a_running_core_that_exits_is_fatal() {
        let (on_lost, lost) = lost_channel();
        let started = Core::start_with(fake(&format!("{READY}\ntime.sleep(1)")), &ShellSecret::generate(), on_lost);
        let (core, bootstrap) = started.expect("ready");
        assert_eq!((core.port, bootstrap.as_str()), (4242, "b"));
        assert_eq!(lost.recv_timeout(Duration::from_secs(10)), Ok("exited"));
    }

    #[test]
    fn a_protocol_violation_kills_the_core_and_is_fatal() {
        let (on_lost, lost) = lost_channel();
        let script = format!("{READY}\ntime.sleep(0.3)\nsys.stdout.write('not json\\n'); sys.stdout.flush()\ntime.sleep(60)");
        let (core, _) = Core::start_with(fake(&script), &ShellSecret::generate(), on_lost).expect("ready");
        assert_eq!(lost.recv_timeout(Duration::from_secs(10)), Ok("protocol violation"));
        let mut child = core.child.lock().unwrap();
        assert!(child.wait().is_ok()); // killed, not left sleeping for 60 s
    }

    #[test]
    fn shutdown_is_not_a_lost_core() {
        let (on_lost, lost) = lost_channel();
        let script = format!("{READY}\nsys.stdin.readline()");
        let (core, _) = Core::start_with(fake(&script), &ShellSecret::generate(), on_lost).expect("ready");
        core.shutdown();
        assert!(lost.recv_timeout(Duration::from_millis(800)).is_err());
    }
}
