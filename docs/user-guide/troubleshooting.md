# Troubleshooting

[한국어](troubleshooting.ko.md) · [User Guide](README.md)

## I cannot see a course/session

- Confirm you are looking at the correct semester.
- Enter the course first; sessions are intentionally not global navigation items.
- If the expected session is still missing, ask the operator to check the exact course relation/binding instead of creating a duplicate record.

## A new file is not being processed

A file appearing in Drive does not automatically prove that intake is configured and ready.

- Open **Files** and check whether an intake/request row exists.
- Make sure any required course/session/date/intent fields are filled.
- Confirm that you explicitly submitted the request and did not cancel it.
- If the request says **Needs Input**, provide the missing information.
- If it says **Reconcile Required** or **Failed**, ask the operator to inspect the worker/provider evidence before retrying.

## The AI cannot find my material

Possible causes include:

- the client is not actually connected to Syllva MCP;
- the material is still Partial/not current;
- the question does not identify enough scope;
- the source is outside the authorized course/session/material range;
- the client profile has not completed live validation in your environment.

Do not solve this by pasting private credentials or entire private source files into a prompt. Ask the operator to check `uls doctor`, MCP registration, and source readiness.

## The AI says context is ambiguous

Choose the correct candidate when the client presents course/session/material options. Ambiguity is a normal fail-closed behavior; guessing the first candidate would be less safe.

## To DO/calendar looks empty

An empty view can simply mean that no real records have been added. Do not create fake assignments or exam dates just to make the dashboard look populated.

## I need operator help

Give the operator the visible status/error code and the affected course/session/request identity. Do not send secrets. Operator diagnostics begin with `uls doctor`, `uls status`, and `uls jobs` as described in the [Operator Guide](../operator-guide/README.md).
