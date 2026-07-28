# Architecture

`conduit-client` is the public source and package boundary for the Python SDK, CLI, admission workflow, schemas, and candidate assessment. `conduit-local` owns the private/self-hosted MCP runtime, workspaces, grants, approval, promotion, revocation, and local administration.

The client never grants authority. It presents operator-issued credentials or enrollment requests to the server and verifies the resulting server-enforced grant.
