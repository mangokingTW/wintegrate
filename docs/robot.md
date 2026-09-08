# Robot Framework

`wintegrate.robot.WintegrateLibrary` exposes the verified operations as Robot
Framework keywords. Nothing is re-implemented: every keyword is a one-line call
into the Python API, and nothing is caught, so a `Type Verified` that times out
fails the step exactly as `type_verified` fails a pytest test.

```
pip install "wintegrate[robot]"
robot --outputdir robot-artifacts examples/robot/notepad.robot
```

## A suite

```robotframework
*** Settings ***
Library           wintegrate.robot.WintegrateLibrary    artifact_dir=robot-artifacts    record_video=True
Suite Setup       Start Session
Suite Teardown    Stop Session

*** Test Cases ***
Typed Text Lands In The Editor
    Given Notepad Is Running
    When I Type    hello from robot\n    expected_line_count_delta=1
    Then The Editor Contains    hello from robot

*** Keywords ***
Notepad Is Running
    ${app}=    Launch App    notepad
    ${editor}=    Find Text Input    ${app}
    Set Suite Variable    ${EDITOR}    ${editor}

I Type
    [Arguments]    ${text}    ${expected_line_count_delta}=${None}
    Type Verified    ${EDITOR}    ${text}    expected_line_count_delta=${expected_line_count_delta}

The Editor Contains
    [Arguments]    ${expected}
    ${value}=    Get Value    ${EDITOR}
    Should Contain    ${value}    ${expected}
```

Robot strips the `Given`/`When`/`Then` prefixes, so the report reads as the
scenario while each step is still a verified call.

## What the library does on its own

The library is also its own listener (`ROBOT_LISTENER_API_VERSION = 3`):

- the running test's name and file are drawn into the recording's caption, so
  one video of the suite stays searchable;
- a failing test gets a full-desktop screenshot, saved under `artifact_dir` and
  embedded in `log.html`;
- `Stop Session` embeds the session recording in the log, and the listener calls
  it at suite end if the suite did not, so a failing setup never leaks a recorder
  or an application.

Everything the Python `Session` writes — event journal, window census, kill plan,
`READ_THIS_FIRST.md` — is written here too, under `artifact_dir`.

## Keywords

| Keyword | Calls |
| --- | --- |
| `Start Session` / `Stop Session` | `Session.__enter__` / `__exit__` |
| `Launch App  name-or-command  fresh=auto` | `Session.app(...)`; `notepad`, `calculator`, or a command line |
| `Close App  ${app}` | `AppHandle.close()` |
| `Find Text Input  ${app}` | `AppHandle.find_text_input()` |
| `Locate  ${app}  selector` / `Get By Role  ${app}  role  name` | Playwright-style locators |
| `Type Verified  ${element}  text  expected_line_count_delta=  verify_contains=` | `UiaElement.type_verified()` |
| `Get Value  ${element}` | `UiaElement.get_value()` |
| `Click  ${target}` | `Locator.click()` / `UiaElement.click()` — raises with no rectangle to aim at |
| `Send Keys  spec` | `wintegrate.interop.send_keys` |
| `Get Focused Element` / `Describe Element` / `Focused Element Class Should Be` | UIA `GetFocusedElement` |
| `Capture Screenshot  name` | `Session.capture_screenshot()`, embedded in the log |
| `Log Event  type  message` | `Session.log_event()` |

Arguments are converted from the suite's strings by Robot using the keyword
type hints; `${None}` passes a Python `None`.

## What does not change

The process model. Robot runs one Python process on the desktop session, the
same as pytest, so everything in [What breaks in CI](pitfalls.md) applies: the
runner's own console may live in a Windows Terminal window, killing by image
name can kill the job, and a flyout that is open hides the controls under it from
the window's UIA tree.
