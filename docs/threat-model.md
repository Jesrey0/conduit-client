# Threat model

The candidate controls the sandbox, verifier source, network, and generated report. The verifier detects accidental or ordinary workflow failures, not adversarial tampering.

It checks exact fixture state, Git preservation, synthetic credential placement/mode, validation nonce, and forbidden local state files. It cannot observe chat disclosure, prove network behavior, verify model identity, or predict future behavior.

Real credentials are never present during assessment. Provisioning uses short-lived one-time invites and server-side manual approval.
