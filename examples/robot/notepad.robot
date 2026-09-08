*** Settings ***
Documentation     Types into Notepad and checks the text arrived, through wintegrate.
...
...               Run with:  robot --outputdir robot-artifacts examples/robot/notepad.robot
...
...               One Notepad for the whole suite, launched in the suite setup; each
...               test is one Given/When/Then scenario against it.
...
...               The library is its own listener: the recording's caption names the
...               running test, a failing test gets a screenshot in log.html, and the
...               session closes at suite end even when a step raised.
Library           wintegrate.robot.WintegrateLibrary    artifact_dir=robot-artifacts    record_video=True
Suite Setup       Run Keywords    Start Session    AND    Notepad Is Running
Suite Teardown    Stop Session

*** Test Cases ***
Typed Text Lands In The Editor
    When I Type    hello from robot\n    expected_line_count_delta=1
    Then The Editor Contains    hello from robot

Escape Leaves Focus In The Editor
    When I Press    {ESC}
    Then Focus Is On The Editor

The Window Is In Front And Counted
    When I Bring It To The Front
    Then Exactly One Notepad Window Is Visible

*** Keywords ***
Notepad Is Running
    ${app}=    Launch App    notepad
    ${editor}=    Find Text Input    ${app}
    Set Suite Variable    ${APP}    ${app}
    Set Suite Variable    ${EDITOR}    ${editor}

I Type
    [Arguments]    ${text}    ${expected_line_count_delta}=${None}
    Type Verified    ${EDITOR}    ${text}    expected_line_count_delta=${expected_line_count_delta}

I Press
    [Arguments]    ${spec}
    Send Keys    ${spec}

The Editor Contains
    [Arguments]    ${expected}
    ${value}=    Get Value    ${EDITOR}
    Should Contain    ${value}    ${expected}

Focus Is On The Editor
    ${focused}=    Get Focused Element
    ${description}=    Describe Element    ${focused}
    Log    focus: ${description}
    Should Be Equal    ${focused.class_name}    ${EDITOR.class_name}

I Bring It To The Front
    Set Foreground    ${APP}

Exactly One Notepad Window Is Visible
    Window Should Exist    class_name=Notepad
    ${n}=    Count Windows    class_name=Notepad
    Should Be Equal As Integers    ${n}    1
