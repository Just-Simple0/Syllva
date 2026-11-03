use serde_json::{json, Map, Value};
use std::io::Cursor;
use syllva_shell_proto::{env, frame, mac, relay_log, secret::ShellSecret};

fn vectors() -> Vec<Value> {
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/../../tests/fixtures/gate_mac_vectors.json");
    serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap()
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

#[test]
fn mac_matches_the_python_core_vectors() {
    let cases = vectors();
    assert!(!cases.is_empty());
    for case in cases {
        let key_hex = case["key_hex"].as_str().unwrap();
        let mut key = [0u8; 32];
        for (i, chunk) in key_hex.as_bytes().chunks(2).enumerate() {
            key[i] = u8::from_str_radix(std::str::from_utf8(chunk).unwrap(), 16).unwrap();
        }
        let tag = mac::compute(&key, case["nonce_id"].as_str().unwrap(), case["request_hash"].as_str().unwrap());
        assert_eq!(hex(&tag), case["mac_hex"].as_str().unwrap());
    }
}

#[test]
fn frames_round_trip_and_carry_the_version() {
    let mut message = Map::new();
    message.insert("t".into(), json!("ping"));
    message.insert("seq".into(), json!(3));
    let line = frame::encode_message(message).unwrap();
    let parsed = frame::read_message(&mut Cursor::new(line)).unwrap();
    assert_eq!(parsed["t"], "ping");
    assert_eq!(parsed["v"], 1);
}

#[test]
fn bad_lines_are_violations_not_skipped() {
    let cases: Vec<(&[u8], frame::FrameError)> = vec![
        (b"not json\n", frame::FrameError::NotJson),
        (b"[1,2]\n", frame::FrameError::NotAMessage),
        (b"{\"t\":\"x\",\"v\":2}\n", frame::FrameError::WrongVersion),
        (b"{\"t\":\"x\",\"v\":true}\n", frame::FrameError::WrongVersion),
        (b"{\"t\":\"x\",\"v\":1}", frame::FrameError::TooLong), // unterminated
        (b"", frame::FrameError::Closed),
    ];
    for (input, expected) in cases {
        assert_eq!(frame::read_message(&mut Cursor::new(input.to_vec())).unwrap_err(), expected);
    }
    let mut long = b"{\"t\":\"".to_vec();
    long.extend(std::iter::repeat_n(b'x', 70_000));
    long.extend(b"\",\"v\":1}\n");
    assert_eq!(frame::read_message(&mut Cursor::new(long)).unwrap_err(), frame::FrameError::TooLong);
}

#[test]
fn core_env_is_an_allowlist() {
    let vars = vec![
        ("PATH", "/bin"), ("Path", "C:\\"), ("HOME", "/h"), ("ANTHROPIC_API_KEY", "x"), ("SYLLVA_DATA_DIR", "/d"),
        ("CANVAS_TOKEN", "t"), ("LANG", "ko_KR.UTF-8"),
    ];
    let kept: Vec<String> = env::core_env(vars).into_iter().map(|(k, _)| k).collect();
    assert_eq!(kept, vec!["PATH", "Path", "HOME", "SYLLVA_DATA_DIR", "LANG"]);
}

#[test]
fn secret_is_random_and_never_printed() {
    let a = ShellSecret::generate();
    let b = ShellSecret::generate();
    assert_ne!(a.bytes(), b.bytes());
    assert_eq!(format!("{a:?}"), "<secret>");
    assert_eq!(ShellSecret::from_bytes([0u8; 32]).hello_value(), "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA");
}

#[test]
fn relay_log_has_no_query_fragment_headers_or_body() {
    let line = relay_log::line("POST", "/api/session?bootstrap=SECRET#frag", 200, 512);
    assert_eq!(line, "relay POST /api/session 200 512");
    assert!(!relay_log::line("GET", "/a\nb?x", 404, 0).contains('\n'));
}
