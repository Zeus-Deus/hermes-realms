"""CLI discovery and concurrent expiry with real qcow2 images and unit reads.

This lane only reads nonexistent systemd units; it never starts a VM, service,
compositor or input driver. Host daemon reload is deliberately suppressed for
the nonexistent policies. All state observations and file-holder checks are real.
"""
import os
import subprocess
import sys

import pytest
from realms_test_paths import HERMES_ROOT, PLUGIN_ROOT, install_user_plugin


@pytest.mark.platforms("linux")
@pytest.mark.integration
def test_real_cli_concurrent_expiry_and_open_disk_protection(tmp_path):
    home = tmp_path / "profile"
    install_user_plugin(home)
    env = {key: os.environ[key] for key in ("PATH", "LANG", "TZ") if key in os.environ}
    env.update(HOME=str(tmp_path), HERMES_HOME=str(home), PYTHONPATH=str(HERMES_ROOT),
               XDG_CONFIG_HOME=str(tmp_path / "xdg"), XDG_DATA_HOME=str(tmp_path / "data"))
    code = r'''
from dataclasses import asdict
import json, os, runpy, shutil, subprocess, sys, time, uuid
from pathlib import Path
from hermes_cli.plugins_discovery import collect_directory_manifests
from hermes_cli.plugins_manifest import manifest_key
root, home = map(Path, sys.argv[1:])
load = runpy.run_path(str(root / 'realms/_binding.py'))['load_runtime']
vm, workspace, lifecycle = map(load, ('vm_manager', 'vm_workspace', 'lifecycle'))
if not shutil.which('qemu-img'):
    print('SKIP: qemu-img unavailable'); sys.exit(77)
try:
    assert lifecycle.scope_info('hermes-vm-' + uuid.uuid4().hex + '.service')['ActiveState'] == 'inactive'
except lifecycle.RealmError:
    print('SKIP: systemd user state unavailable'); sys.exit(77)
manifests = collect_directory_manifests()
assert any(m.name == 'hermes-realms' for m in manifests)
other = [manifest_key(m) for m in manifests if m.name != 'hermes-realms']
(home / 'config.yaml').write_text(json.dumps({'plugins': {'enabled': ['hermes-realms'], 'disabled': other}}))
manager = vm.VmManager(home)
base = manager.base_home()
base.mkdir(parents=True, mode=0o700)
subprocess.run(['qemu-img', 'create', '-q', '-f', 'qcow2', str(base / 'disk.qcow2'), '16M'], check=True)
lifecycle.atomic_json(base / 'base.json', {'iso': 'omarchy-test.iso'})
(base / 'OVMF_VARS.4m.fd').write_bytes(b'nonbooted firmware fixture')
iso = manager.data / 'iso' / 'omarchy-test.iso'
iso.parent.mkdir(); iso.write_bytes(b'installer fixture')
records = []
for name, days in [('expired', 15), ('held', 15), ('recent', 1)]:
    gen = uuid.uuid4().hex
    session = home / 'realms' / 'vm' / gen
    session.mkdir(parents=True, mode=0o700)
    runtime = vm._generation_paths(os.getuid(), gen)
    assert not runtime.exists()
    record = dict(id='v-' + gen[:24], generation=gen, uid=os.getuid(), home=str(home),
                  session_id=name, kind=manager.KIND, status='stopped', cleanup_required=False,
                  runtime_dir=str(runtime), session_dir=str(session), base_disk=str(base / 'disk.qcow2'),
                  unit='hermes-vm-' + gen + '.service', guardian_unit='hermes-vm-' + gen + '-guard.service',
                  owner_protocol='pidfd-v2', memory=manager.config.vm.memory, network=manager.config.vm.network,
                  stopped_at=time.time() - days * 86400, last_activity=time.time() - days * 86400)
    subprocess.run(['qemu-img', 'create', '-q', '-f', 'qcow2', '-b', record['base_disk'], '-F', 'qcow2',
                    str(session / 'disk.qcow2')], check=True)
    (session / 'OVMF_VARS.4m.fd').write_bytes(b'nonbooted firmware fixture')
    lifecycle.atomic_json(session / 'spec.json', asdict(manager.config))
    (session / 'ssh_known_hosts').write_bytes(b'enrolled pin fixture')
    (session / 'ssh_known_hosts').chmod(0o600)
    workspace.create(record)
    record['workspace']['pin'] = workspace._file(session / 'ssh_known_hosts')
    manager.registry.put(record)
    records.append(record)
# These fixtures never had owner policies; verify absence rather than reloading
# the host user manager as the production remove_dropin retry would do.
child = r"""
import runpy, sys
from pathlib import Path
root = Path(sys.argv[1])
load = runpy.run_path(str(root / 'realms/_binding.py'))['load_runtime']
lifetime = load('vm_owner_lifetime')
def absent_policy(record):
    assert not lifetime.dropin_directory(record).exists()
    assert all(load('lifecycle').scope_info(unit)['ActiveState'] in {'inactive','failed'}
               for unit in (record['unit'], lifetime.owner_unit(record), record['guardian_unit']))
lifetime.remove_dropin = absent_policy
from hermes_cli.main import _build_cli_parser
parser, _ = _build_cli_parser()
args = parser.parse_args(['realms', 'vm', 'list', '--json'])
sys.exit(args.func(args))
"""
held = Path(records[1]['session_dir']) / 'disk.qcow2'
holder = subprocess.Popen([sys.executable, '-c',
    "import sys; f=open(sys.argv[1],'rb'); print('ready',flush=True); sys.stdin.read()", str(held)],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
try:
    assert holder.stdout.readline().strip() == 'ready'
    children = [subprocess.Popen([sys.executable, '-c', child, str(root)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
    for process in children:
        stdout, stderr = process.communicate(timeout=40)
        assert process.returncode == 0, stdout + stderr
        assert {r['session_id'] for r in json.loads(stdout)} == {'held', 'recent'}
    assert not Path(records[0]['session_dir']).exists()
    assert held.exists()
finally:
    holder.communicate(timeout=10)
result = subprocess.run([sys.executable, '-c', child, str(root)], capture_output=True, text=True, timeout=40)
assert result.returncode == 0, result.stdout + result.stderr
assert [r['session_id'] for r in json.loads(result.stdout)] == ['recent']
assert not held.exists()
assert (base / 'disk.qcow2').exists() and iso.read_bytes() == b'installer fixture'
print('real CLI: discovery, qcow2 backing, concurrent cleanup, real inactive-unit reads and holder protection passed')
'''
    result = subprocess.run([sys.executable, "-c", code, str(PLUGIN_ROOT), str(home)],
                            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120)
    if result.returncode == 77:
        pytest.skip(result.stdout.strip())
    assert result.returncode == 0, result.stdout + result.stderr
