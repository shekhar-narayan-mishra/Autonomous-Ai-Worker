# Autonomous AI Task Worker - Demo Script

**Total Time**: ~3-4 minutes

## 1. Introduction (0:30)
- "This is an autonomous AI worker built from scratch to interact directly with internal corporate tools, purely via natural language."
- Run `./run_demo.sh` in the terminal to spin up the Vendor Portal, the internal ERP, and the Agent UI.
- Open the UI at `http://localhost:8000`. Show the environment config panel, demonstrating how the agent dynamically learns about available apps and credentials. 

## 2. The Happy Path (1:00)
- In the UI, use the default task: *"Find the latest invoice from Acme Corp, extract the amount and due date, and enter it into the ERP."*
- Uncheck "Auto-Approve".
- Hit **Run Task**.
- **What to highlight**:
  - The Live Timeline: Show how the agent outputs structured reasoning (`thought`), executes tools (`action`), and captures observations from the DOM. 
  - The Screenshots: The agent creates snapshots mapping elements to IDs, proving it does not use a visual foundation model, but rather a compact, TPM-friendly representation.
  - The Approval Modal: Since the ERP involves writes, a modal will pop up. Explain the safety risk-tiering. Click **Approve**.
- Wait for completion. Show the final verifier table turning green.

## 3. Chaos and Recovery (1:00)
- Change the Chaos Flag dropdown to **Validation Error on 1st Submit**.
- Check "Auto-Approve" (to save time).
- Hit **Run Task**.
- **What to highlight**:
  - The agent will fill out the ERP form and hit submit, but the server will randomly throw a validation error.
  - The timeline will show the agent catching the error, emitting a "Retry" thought, and correcting its action autonomously without human intervention.
  - The Verifier panel at the end verifies the state of the DB independently of the agent's claims.

## 4. Ambiguity and Clarification (1:00)
- Change the task to: *"Find the latest invoice from Globex and enter it into the ERP."*
- Set Vendor Name to `Globex`.
- Hit **Run Task**.
- **What to highlight**:
  - Globex has duplicate/ambiguous invoices seeded in the Vendor Portal. The agent's prompt dictates that it must not guess.
  - A "Clarification Required" modal will pop up.
  - Show how the agent paused execution to ask the human which invoice to select. 
  - Type `INV-AMB-1` and hit submit. The agent resumes, finds it, and enters it successfully. 

## 5. Generalization & Eval Table (0:30)
- Change the task to a completely re-phrased instruction: *"Look up Acme Corp's most recent bill on the vendor site, grab the total and date, and punch it into the accounting system."*
- Show that without writing a single line of new regex or code, the agent handles it. 
- Open `eval/results.md` to show the comprehensive 60-run evaluation table across all edge cases (missing invoices, mismatched logic, chaos). Explain that the core architecture is 100% generic. 
