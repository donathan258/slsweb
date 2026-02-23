# SLS Certificate & Name Tent Generator

A web tool for the **Order of the Arrow Section Leadership Seminar (SLS)** that generates personalised participant certificates and name tents from a CSV file.

---

## What It Does

Upload a CSV with your attendee list and the app will produce:

- **Participant certificates** for attendees
- **Staff certificates** for SLS staff members
- **Name tents** for use at the event

You can generate all three together as a ZIP, or certificates and name tents individually.

---

## How to Use

### 1. Prepare your CSV

Create a spreadsheet with three columns — **Name**, **Lodge**, and **Role** — and save it as a `.csv` file.

| Name | Lodge | Role |
|------|-------|------|
| Christopher Grove | Tipisa Lodge | Participant |
| Brea Baygents | Wewikit Lodge | Participant |
| Cortland Bolles | Wewikit Lodge | Staff |
- **Role** must be either `Staff` or `Participant` — this determines which certificate template is used
- Not sure about the format? Download the sample CSV from the app for a ready-to-edit starting point

### 2. Enter your section

Select your region (Eastern or Gateway) and enter JUST your section number. The app will automatically format it as e.g. **Section E9**.

### 3. Choose what to generate

Select whether you want certificates, name tents, or both, then click **Generate**.

### 4. Download

Your documents will download automatically. If you selected both, they arrive as a ZIP containing `Certificates.pdf` and `Name_Tents.pdf`.

---

## Tips

- Names, lodges, and section are printed exactly as entered — double-check spelling before generating
- Role is case-insensitive — `staff`, `Staff`, and `STAFF` all work
