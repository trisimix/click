# Copyright (C) 2014 Canonical Ltd.
# Author: Michael Vogt <michael.vogt@ubuntu.com>

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; version 3 of the License.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

"""Integration tests for the click signature checking."""

import copy
import os
import shutil
import subprocess
import tarfile
from textwrap import dedent

from .helpers import ClickTestCase

def makedirs(path):
    try:
        os.makedirs(path)
    except OSError:
        pass


class Debsigs:
    """Tiny wrapper around the debsigs CLI"""
    def __init__(self, gpghome, keyid):
        self.keyid = keyid
        self.gpghome = gpghome
        self.policy = "/etc/debsig/policies/%s/generic.pol" % self.keyid

    def sign(self, filepath, signature_type="origin"):
        """Sign the click at filepath"""
        env = copy.copy(os.environ)
        env["GNUPGHOME"] = os.path.abspath(self.gpghome)        
        subprocess.check_call(
            ["debsigs",
             "--sign=%s" % signature_type,
             "--default-key=%s" % self.keyid,
             filepath], env=env)

    def install_signature_policy(self):
        """Install/update the system-wide signature policy"""
        xmls = dedent("""\
        <?xml version="1.0"?>
        <!DOCTYPE Policy SYSTEM "http://www.debian.org/debsig/1.0/policy.dtd">
        <Policy xmlns="http://www.debian.org/debsig/1.0/">

        <Origin Name="test-origin" id="{keyid}" Description="Example policy"/>
        <Selection>
        <Required Type="origin" File="{filename}" id="{keyid}"/>
        </Selection>
  
        <Verification>
        <Required Type="origin" File="{filename}" id="{keyid}"/>
        </Verification>
        </Policy>
        """.format(keyid=self.keyid, filename="origin.pub"))
        makedirs(os.path.dirname(self.policy))
        with open(self.policy, "w") as f:
            f.write(xmls)
        pubkey_path = "/usr/share/debsig/keyrings/%s/origin.pub" % self.keyid
        makedirs(os.path.dirname(pubkey_path))
        shutil.copy(os.path.join(self.gpghome, "pubring.gpg"), pubkey_path)

    def uninstall_signature_policy(self):
        os.remove(self.policy)


class ClickSignaturesTestCase(ClickTestCase):
    def assertClickNoSignatureError(self, cmd_args):
        with self.assertRaises(subprocess.CalledProcessError) as cm:
            output = subprocess.check_output(
                [self.click_binary] + cmd_args,
                stderr=subprocess.STDOUT, universal_newlines=True)
        output = cm.exception.output
        expected_error_message = ("debsig: Origin Signature check failed. "
                                  "This deb might not be signed.")
        self.assertIn(expected_error_message, output)

    def assertClickInvalidSignatureError(self, cmd_args):
        with self.assertRaises(subprocess.CalledProcessError) as cm:
            output = subprocess.check_output(
                [self.click_binary] + cmd_args,
                stderr=subprocess.STDOUT, universal_newlines=True)
        output = cm.exception.output
        expected_error_message = "Signature verification failed: "
        self.assertIn(expected_error_message, output)


class TestSignatureVerificationNoSignature(ClickSignaturesTestCase):
    def test_debsig_verify_no_sig(self):
        name = "com.ubuntu.debsig-no-sig"
        path_to_click = self._make_click(name, framework="")
        self.assertClickNoSignatureError(["verify", path_to_click])

    def test_debsig_install_no_sig(self):
        name = "com.ubuntu.debsig-no-sig"
        path_to_click = self._make_click(name, framework="")
        self.assertClickNoSignatureError(["install", path_to_click])

    def test_debsig_install_can_install_with_sig_override(self):
        name = "com.ubuntu.debsig-no-sig"
        path_to_click = self._make_click(name, framework="")
        user = os.environ.get("USER", "root")
        subprocess.check_call(
            [self.click_binary, "install",
             "--allow-unauthenticated", "--user=%s" % user,
             path_to_click])
        self.addCleanup(
            subprocess.call, [self.click_binary, "unregister",
                              "--user=%s" % user, name])


class TestSignatureVerification(ClickSignaturesTestCase):
    def setUp(self):
        super(TestSignatureVerification, self).setUp()
        self.user = os.environ.get("USER", "root")
        # the valid origin keyring
        self.datadir = os.path.join(os.path.dirname(__file__), "data")
        origin_keyring_dir = os.path.join(self.datadir, "origin-keyring")
        self.debsigs = Debsigs(origin_keyring_dir, "8354C8099FD1B9DA")
        self.debsigs.install_signature_policy()

    def tearDown(self):
        self.debsigs.uninstall_signature_policy()

    def test_debsig_install_valid_signature(self):
        name = "com.ubuntu.debsig-valid-sig"
        path_to_click = self._make_click(name, framework="")
        self.debsigs.sign(path_to_click)
        subprocess.check_call(
            [self.click_binary, "install",
             "--user=%s" % self.user,
             path_to_click])
        self.addCleanup(
            subprocess.call, [self.click_binary, "unregister",
                              "--user=%s" % self.user, name])
        output = subprocess.check_output(
            [self.click_binary, "list", "--user=%s" % self.user],
            universal_newlines=True)
        self.assertIn(name, output)
        
    def test_debsig_install_signature_not_in_keyring(self):
        name = "com.ubuntu.debsig-no-keyring-sig"
        path_to_click = self._make_click(name, framework="")
        evil_keyring_dir = os.path.join(self.datadir, "evil-keyring")
        debsig_bad = Debsigs(evil_keyring_dir, "18B38B9AC1B67A0D")
        debsig_bad.sign(path_to_click)
        # and ensure its really not there
        self.assertClickInvalidSignatureError(["install", path_to_click])
        output = subprocess.check_output(
            [self.click_binary, "list", "--user=%s" % self.user],
            universal_newlines=True)
        self.assertNotIn(name, output)

    def test_debsig_install_not_a_signature(self):
        name = "com.ubuntu.debsig-invalid-sig"
        path_to_click = self._make_click(name, framework="")
        invalid_sig = os.path.join(self.temp_dir, "_gpgorigin")
        with open(invalid_sig, "w") as f:
            f.write("no-valid-signature")
        # add a invalid sig
        subprocess.check_call(["ar", "-r", path_to_click, invalid_sig])
        self.assertClickInvalidSignatureError(["install", path_to_click])
        output = subprocess.check_output(
            [self.click_binary, "list", "--user=%s" % self.user],
            universal_newlines=True)
        self.assertNotIn(name, output)

    def test_debsig_install_signature_altered_click(self):
        def modify_ar_member(member):
            subprocess.check_call(
                ["ar", "-x", path_to_click, "control.tar.gz"],
                cwd=self.temp_dir)
            altered_control = os.path.join(self.temp_dir, member)
            with open(altered_control, "ba") as f:
                f.write(b"\0")
            subprocess.check_call(["ar", "-r", path_to_click, altered_control])

        # ensure that all members we care about are checked by debsig-verify
        for member in ["control.tar.gz", "data.tar.gz", "debian-binary"]:
            name = "com.ubuntu.debsig-altered-click"
            path_to_click = self._make_click(name, framework="")
            self.debsigs.sign(path_to_click)
            modify_ar_member(member)
            self.assertClickInvalidSignatureError(["install", path_to_click])
            output = subprocess.check_output(
                [self.click_binary, "list", "--user=%s" % self.user],
                universal_newlines=True)
            self.assertNotIn(name, output)

    def test_debsig_install_signature_injected_data_tar(self):
        name = "com.ubuntu.debsig-injected-data-click"
        path_to_click = self._make_click(name, framework="")
        self.debsigs.sign(path_to_click)
        new_data = os.path.join(self.temp_dir, "data.tar.bz2")
        evilfile = os.path.join(self.temp_dir, "README.evil")
        with open(evilfile, "w") as f:
            f.write("I am a nasty README")
        with tarfile.open(new_data, "w:bz2") as tar:
            tar.add(evilfile)
        # insert before the real data.tar.gz and ensure this is caught
        # NOTE: that right now this will not be caught by debsig-verify
        #        but later in audit() by debian.debfile.DebFile()
        subprocess.check_call(["ar",
                               "-r", 
                               "-b", "data.tar.gz",
                               path_to_click,
                               new_data])
        output = subprocess.check_output(
            ["ar", "-t", path_to_click], universal_newlines=True)
        self.assertEqual(output.splitlines(),
                         ["debian-binary",
                          "_click-binary",
                          "control.tar.gz",
                          "data.tar.bz2",
                          "data.tar.gz",
                          "_gpgorigin"])
        with self.assertRaises(subprocess.CalledProcessError):
            output = subprocess.check_output(
                [self.click_binary, "install", path_to_click],
                stderr=subprocess.STDOUT, universal_newlines=True)
        output = subprocess.check_output(
            [self.click_binary, "list", "--user=%s" % self.user],
            universal_newlines=True)
        self.assertNotIn(name, output)

    def test_debsig_install_signature_replaced_data_tar(self):
        name = "com.ubuntu.debsig-replaced-data-click"
        path_to_click = self._make_click(name, framework="")
        self.debsigs.sign(path_to_click)
        new_data = os.path.join(self.temp_dir, "data.tar.bz2")
        evilfile = os.path.join(self.temp_dir, "README.evil")
        with open(evilfile, "w") as f:
            f.write("I am a nasty README")
        with tarfile.open(new_data, "w:bz2") as tar:
            tar.add(evilfile)
        # replace data.tar.gz with data.tar.bz2 and ensure this is caught
        subprocess.check_call(["ar",
                               "-d", 
                               path_to_click,
                               "data.tar.gz",
                               ])
        subprocess.check_call(["ar",
                               "-r", 
                               path_to_click,
                               new_data])
        output = subprocess.check_output(
            ["ar", "-t", path_to_click], universal_newlines=True)
        self.assertEqual(output.splitlines(),
                         ["debian-binary",
                          "_click-binary",
                          "control.tar.gz",
                          "_gpgorigin",
                          "data.tar.bz2",
                         ])
        with self.assertRaises(subprocess.CalledProcessError) as cm:
            output = subprocess.check_output(
                [self.click_binary, "install", path_to_click],
                stderr=subprocess.STDOUT, universal_newlines=True)
            self.assertIn("Signature verification failed", cm.exception.output)
        output = subprocess.check_output(
            [self.click_binary, "list", "--user=%s" % self.user],
            universal_newlines=True)
        self.assertNotIn(name, output)
        
