# SSH Module

## Module
`plugins.ssh_module`

## Class
`SSHModule`

## Methods
- `run_command`: Run a shell command remotely via SSH
- `list_directory`: List contents of a directory remotely

## Usage
For remote server management and automation via SSH.

## Example
```json
{
  "module": "plugins.ssh_module",
  "class": "SSHModule",
  "method": "run_command",
  "args": ["ls -la"]
}
```
