# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 2.0.x   | ✅ Active          |
| 1.3.x   | ⚠️ Security fixes only |
| < 1.3   | ❌ Not supported   |

## Reporting a Vulnerability

We take the security of LuckyD Code seriously. If you believe you've found a
security vulnerability, please **do not** open a public issue.

**Instead, report it privately:**

1. **Email**: security@luckyd.ai
2. **PGP Key**: Available on request
3. **Response Time**: We aim to respond within 48 hours

### What to Include

- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Any suggested fixes (optional)

### What Happens Next

1. We acknowledge receipt within 48 hours
2. We investigate and determine severity
3. We develop and test a fix
4. We release a patch and disclose responsibly

## Security Best Practices

### For Users

1. **Never commit `.env` files** with real API keys to version control
2. **Use environment variables** instead of hardcoded secrets
3. **Rotate API keys** regularly
4. **Review tool commands** before execution (the agent asks for confirmation)

### For Developers

1. **Run security scans** before merging: `make security`
2. **Handle secrets carefully** — never log or expose API keys
3. **Validate all inputs** — especially tool parameters
4. **Use the sandbox** — command execution is blocked for dangerous patterns
5. **Keep dependencies updated** — regularly run `safety check`

## Known Security Features

- **Command sandboxing**: Dangerous commands are blocked (rm -rf /, format, etc.)
- **API key redaction**: Keys are automatically redacted from logs
- **Path validation**: Writes outside project directory are blocked
- **Input sanitization**: All tool inputs are validated
- **Dependency scanning**: CI pipeline checks for vulnerable packages
