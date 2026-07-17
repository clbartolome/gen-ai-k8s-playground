# How to delete a virtual machine

This runbook describes the standard procedure to safely decommission a virtual machine.

## Prerequisites

- Confirmed VM identity (hostname, ID, environment)
- Owner approval for deletion
- Verification that the VM is no longer in use

## Procedure

1. **Verify the target VM** — Confirm the correct VM with the owner or requester. Document hostname, environment, and reason for deletion.
2. **Open or link an ITSM ticket** — Create a deletion request in ITSM or link an existing ticket. Record the VM details and approval.
3. **Run the AAP workflow** — Execute the `delete_vm` workflow in Ansible Automation Platform (AAP). This workflow will be available through the automation MCP integration. Provide the ticket ID and VM identifier as workflow inputs.
4. **Confirm deletion** — Verify the VM and associated resources have been removed.
5. **Close the ITSM ticket** — Update the ticket with deletion confirmation, timestamp, and any workflow output. Resolve or close the ticket.

## Related

- ITSM: ticket required for audit trail
- AAP workflow: `delete_vm`
- Tags: vm, delete, virtual machine, itsm, aap, workflow, decommission
