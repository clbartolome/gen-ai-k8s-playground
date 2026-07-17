# How to create a virtual machine

This runbook describes the standard procedure to provision a new virtual machine in the platform.

## Prerequisites

- Approved VM request with owner and business justification
- Network and sizing details ready (CPU, memory, disk, network zone)

## Procedure

1. **Open an ITSM ticket** — Create a change or service request in ITSM with the VM specifications: requested name, size, network, environment, and owner contact.
2. **Run the AAP workflow** — Execute the `create_vm` workflow in Ansible Automation Platform (AAP). This workflow will be available through the automation MCP integration. Provide the ticket ID and VM parameters as workflow inputs.
3. **Verify provisioning** — Confirm the VM was created successfully and is reachable. Collect hostname, IP address, and initial status.
4. **Update the ITSM ticket** — Add the outcome to the ticket: hostname, IP, provisioning status, and any notes from the workflow run.
5. **Notify the requester** — Confirm completion with the requester and close or resolve the ticket once validated.

## Related

- ITSM: ticket required before and after automation
- AAP workflow: `create_vm`
- Tags: vm, create, virtual machine, itsm, aap, workflow, provision
