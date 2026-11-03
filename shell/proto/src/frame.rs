//! JSON Lines over the Core's stdio (spec §2.3): one object per line, at most 64 KiB,
//! every message carries `"v": 1`. A line that is not a JSON object is a protocol
//! violation — the Shell never skips it (R2 plan rev 2, item 8).

use serde_json::{Map, Value};
use std::io::BufRead;

pub const MAX_LINE: usize = 64 * 1024;
pub const VERSION: u64 = 1;

#[derive(Debug, PartialEq, Eq)]
pub enum FrameError {
    Closed,
    TooLong,
    NotJson,
    NotAMessage,
    WrongVersion,
    Io(String),
}

/// Read one message. `Closed` means the Core closed stdout (it exited).
pub fn read_message<R: BufRead>(reader: &mut R) -> Result<Map<String, Value>, FrameError> {
    let mut line = Vec::new();
    let mut limited = std::io::Read::take(reader.by_ref(), (MAX_LINE + 1) as u64);
    let n = limited.read_until(b'\n', &mut line).map_err(|e| FrameError::Io(e.kind().to_string()))?;
    if n == 0 {
        return Err(FrameError::Closed);
    }
    if line.len() > MAX_LINE || line.last() != Some(&b'\n') {
        return Err(FrameError::TooLong);
    }
    let value: Value = serde_json::from_slice(&line).map_err(|_| FrameError::NotJson)?;
    let Value::Object(map) = value else { return Err(FrameError::NotAMessage) };
    if !map.get("t").is_some_and(Value::is_string) {
        return Err(FrameError::NotAMessage);
    }
    match map.get("v") {
        Some(Value::Number(n)) if n.as_u64() == Some(VERSION) => Ok(map),
        _ => Err(FrameError::WrongVersion),
    }
}

/// Encode one message as a line (adds `"v": 1`). Fails if the line would exceed the limit.
pub fn encode_message(mut message: Map<String, Value>) -> Result<Vec<u8>, FrameError> {
    message.insert("v".into(), Value::from(VERSION));
    let mut out = serde_json::to_vec(&Value::Object(message)).map_err(|_| FrameError::NotJson)?;
    out.push(b'\n');
    if out.len() > MAX_LINE {
        return Err(FrameError::TooLong);
    }
    Ok(out)
}
