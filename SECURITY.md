# Security Policy

## Purpose

This tool was built for **authorized security research and educational use only**.  
It documents and demonstrates known vulnerabilities across 6 major WiFi smart bulb protocols, helping users understand and assess their own IoT exposure.

## Responsible Disclosure

Protocol vulnerabilities documented by this tool have been responsibly disclosed where applicable:

| Vendor | Contact | CVE |
|---|---|---|
| Signify (WiZ / Philips) | vulnerabilities@signify.com | CVE-2019-18980 + new findings (CAN-2026-2040961, CAN-2026-2040964) |
| Tuya | security@tuya.com | CVE-2019-18693 (prior art, documented here for awareness) |

## Intended Use

This tool is intended for:

- Security researchers auditing devices they **own**
- IoT security education and awareness
- CTF challenges and lab environments
- Network administrators assessing their own smart home infrastructure
- Penetration testers with explicit written authorization

**Do not use this tool against devices or networks you do not own or have explicit written permission to test.**  
Unauthorized access to computer systems is illegal under the CFAA, Computer Misuse Act, and equivalent laws worldwide.

## Exploitability Labels

The `[HACKED]` / `[VULN]` / `[SAFE]` labels shown in tool output reflect the **technical exploitability** of a device on your own network — they are audit result indicators, not instructions or encouragement to attack systems.

## Reporting a Vulnerability

If you discover a vulnerability in this tool itself, please report it via GitHub's private vulnerability reporting or email:

**anmol221999@gmail.com**

Please do not open a public issue for security vulnerabilities.

## CVE References

| CVE | Protocol | Description |
|---|---|---|
| CVE-2019-18980 | WiZ | Unauthenticated LAN control and info disclosure |
| CVE-2019-18693 | Tuya v3.1 | Unauthenticated DP_QUERY and DP_COMMAND |

## Author

**Anmol K Sachan** ([@FR13ND0x7F](https://twitter.com/FR13ND0x7F)) — Independent Security Researcher
