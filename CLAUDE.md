You are a python expert.
We are working on a non-regression test suite for SOFA framework that is under "/workspace/Regression/SofaRegressionProgram", you cannot touch the other folder in writting.
The goal of this script is to run all scene found under configuration files and compare results to references.
It is python-based


Under "WEAKNESSES.md" there is a list of weaknesses of the scripts. Two famillies are present : critical and not-critical.
For each critial flaw I have written a DECISION (if we should fix it or not) with some indications.

Your work is to go through all of those critical flaws, and to what the DECISION tells you to do.
--> Each flaw = one commit

WARNING : When commiting don't put yourself as co-author otherwite the commit will not be accepted per condition of use of the plugin.

## Environment handling
To run the scripts you'll need to have a compiled version of SOFA. It is present under "/workspace/sofa/.pivi/envs/supported-plugins-dev/sofa-build/".
To use it you'll need to activate the related pixi environement by calling `eval "$(pixi shell-hook -e supported-plugins-dev)"` inside the folder "/workspace/sofa"
Of course the SOFA sources are under "/workspace/sofa"

## How to work
- If you hit a wall where you need to ask a question on a point that is not clear, save the diff for the current flaw that you are fixing, save the question, and move on to the next one.
- I want you to perform as much task as possible without asking me questions. You should try each flaw before coming back with your questions.

--> ASK me directly to go into auto mode : I want you to be autonomous

Do a first pass to check every thing that'll need to know before going through the work, ask me if anything is unclear.

## Authorizations
DON'T WRITE INTO /workspace/sofa
The repository where you'll need to work is under /workspace/Regression. More precisely, the only folder you'll need t otouch is /workspace/Regression/SofaRegressionProgram
