# ABET Mapper — Instructor Instructions

ABET Mapper is a local application that reads Canvas course, assessment, rubric, and submission data and creates anonymized ABET reports. It does not write grades or other information back to Canvas.

## What you need

- Python 3.10 or newer
- A Canvas API token with access to the courses you want to report on
- An internet connection while loading data from Canvas

## One-time setup

Open Terminal (macOS) or PowerShell (Windows), change into this folder, and install the one required Python package.

### macOS

```bash
python3 -m pip install -r requirements.txt
```

### Windows

```powershell
python -m pip install -r requirements.txt
```

## Start the application

### macOS — easiest method

Double-click `start_abet_mapper.command`. If macOS blocks the first launch, right-click the file, choose **Open**, and confirm.

The script asks for the Canvas token without displaying it, then starts the application.

### macOS — Terminal method

```bash
read -s ABET_MAPPER_CANVAS_TOKEN
export ABET_MAPPER_CANVAS_TOKEN
python3 server.py
```

### Windows — PowerShell

```powershell
.\start_abet_mapper.ps1
```

If PowerShell script execution is restricted, run the application directly for that session:

```powershell
powershell -ExecutionPolicy Bypass -File .\start_abet_mapper.ps1
```

After startup, open this address in a browser:

```text
http://127.0.0.1:8765
```

Keep the Terminal or PowerShell window open while using the application. Press `Control+C` there to stop it.

## Files and privacy

- The Canvas token is kept in memory only and is lost when the application stops.
- Canvas requests made by ABET Mapper are read-only GET requests.
- Reports and local snapshots are written to the `ABET Mapper Data` folder inside this application folder.
- Excel and HTML reports are saved inside `ABET Mapper Data/reports`.
- Course mapping and calculation snapshots are saved inside `ABET Mapper Data/projects`.
- Do not share the `ABET Mapper Data` folder because snapshots can contain student information.
- The data folder is excluded from Git by `.gitignore`, so reports and student information are not uploaded when this repository is pushed.

To save data somewhere else, set `ABET_MAPPER_DATA_DIR` before starting the application.

## Typical workflow

1. Start ABET Mapper and open `http://127.0.0.1:8765`.
2. Select a Canvas course.
3. Add the ABET outcomes being assessed.
4. Select individual assessments, rubric criteria, or an entire Canvas assignment group as evidence. An entire group uses whole-assessment scores only and counts as one KPI.
5. Adjust an Attains threshold when needed.
6. Calculate attainment.
7. Export the Excel report, HTML report, or reusable mapping JSON.

The HTML report contains collapsible student-detail sections. The Excel and HTML reports use the same report content.

## Publish or update on GitHub

This folder is already a Git repository on the `main` branch. To publish it:

1. Create an empty repository on GitHub without adding a README or `.gitignore` there.
2. From this folder, run:

```bash
git remote add origin https://github.com/YOUR-ACCOUNT/YOUR-REPOSITORY.git
git push -u origin main
```

You can alternatively add this existing repository in GitHub Desktop and choose **Publish repository**.

For later updates, commit the application changes and run `git push`. The ignored `ABET Mapper Data` directory will not be included.
