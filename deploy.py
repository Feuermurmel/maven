#! /usr/bin/env python3.3

import sys, os, subprocess, tempfile, argparse, contextlib


def log(msg, *args):
	print('{}: {}'.format(os.path.basename(sys.argv[0]), msg.format(*args)), file = sys.stderr)


def strip_prefix(str, prefix):
	assert str.startswith(prefix)
	
	return str[len(prefix):]


def command(*args, cwd = None, output = False, exit_code = False):
	stdout = subprocess.PIPE if output else None
	stderr = subprocess.PIPE if exit_code else None
	proc = subprocess.Popen(args, cwd = cwd, stdout = stdout, stderr = stderr)
	out, _ = proc.communicate()
	
	if exit_code:
		return proc.returncode
	else:
		if proc.returncode:
			raise Exception('Command failed with exit status {}: {}'.format(proc.returncode, ' '.join(args)))
		
		return out


def maven(dir, goal, **properties):
	properties = ['-D{}={}'.format(k, v) for k, v in properties.items()]
	
	command('mvn', goal, *properties, cwd = dir)


def maven_versions_set(dir, version):
	maven(dir, 'versions:set', newVersion = version)


def maven_deploy(dir, deploy_dir):
	options = { 'altDeploymentRepository': '-::default::file://{}'.format(os.path.abspath(deploy_dir)), 'maven.test.skip': 'true' }
	
	maven(dir, 'deploy', **options)


def git(*args, git_dir = None, work_tree = None, output = False, exit_code = False, cwd = None):
	options = [('git-dir', git_dir), ('work-tree', work_tree)]
	args = ['--{}={}'.format(k, v) for k, v in options if v] + list(args)
	
	return command('git', *args, output = output, exit_code = exit_code, cwd = cwd)


def git_get_repo(dir):
	path, = git('rev-parse', '--git-dir', cwd=dir, output=True).decode().splitlines()
	
	return os.path.join(dir, path)


def git_name_rev(repo, rev, prefix = 'release/'):
	pattern = '{}*'.format(prefix)
	
	res, = git('name-rev', '--no-undefined', '--name-only', '--tags', '--refs={}'.format(pattern), rev, git_dir = repo, output = True).decode().splitlines()
	
	# Strip eventual '^0' suffix used on tags by newer versions of Git.
	res, *rest = res.rsplit('^', 1)
	
	if rest:
		rest, = rest
		
		assert rest == '0'
	
	return strip_prefix(res, prefix)


def git_tag(repo, tag, rev):
	git('tag', tag, rev, git_dir = repo)


def git_checkout(repo, work_tree, rev):
	git('checkout', rev, '.', git_dir= repo, work_tree = work_tree)


def git_reset(repo, rev):
	git('reset', '--soft', rev, git_dir = repo)


def git_clone(src_repo, dst_repo):
	git('clone', '--mirror', src_repo, dst_repo)


def git_init(repo):
	git('init', '--bare', repo)


def git_commit_all(repo, work_tree, message):
	git('add', '--all', '.', git_dir = repo, work_tree = work_tree)
	git('commit', '--message={}'.format(message), git_dir = repo, work_tree = work_tree)


def git_push(src_repo, dst_repo, *refs):
	def refs_fn(ref):
		if isinstance(ref, str):
			ref = ref, ref
		
		return '{}:{}'.format(*ref)
	
	git('push', dst_repo, *map(refs_fn, refs), git_dir = src_repo)


def git_ref_exists(repo, ref):
	return git('rev-parse', ref, git_dir = repo, exit_code = True) == 0


def parse_args():
	parser = argparse.ArgumentParser(description = 'Deploy the project to a separate branch in the repository which hosts this script.')
	parser.add_argument('--debug', action = 'store_true', help = 'Place temporary files in the current working directory and do not clean them up.')
	parser.add_argument('--branch', default = 'mvn-repo', help = 'Branch to use as the Maven repository. Defaults to "mvn-repo"')
	parser.add_argument('--release', metavar = 'version', default = None, help = 'Automatically create tag with this version number before deploying it.')
	parser.add_argument('--push', action = 'store_true', help = 'Automatically push the repository branch to the remote "origin". If used with --relase, the created tag is also pushed.')
	parser.add_argument('revision', nargs = '?', default = 'HEAD', help = 'Revision to deploy.')
	
	return parser.parse_args()


@contextlib.contextmanager
def make_temp_dir(debug):
	if debug:
		base_dir = 'tmp'
		
		os.makedirs(base_dir, exist_ok = True)
		
		# Do not clean up the temporary directory.
		yield tempfile.mkdtemp(dir = base_dir)
	else:
		with tempfile.TemporaryDirectory() as temp_dir:
			yield temp_dir


def main():
	args = parse_args()
	repo = git_get_repo(os.getcwd())
	version = args.release if args.release else git_name_rev(repo, args.revision)
	
	with make_temp_dir(args.debug) as temp_dir:
		work_dir = os.path.join(temp_dir, 'work')
		deploy_dir = os.path.join(temp_dir, 'repo')
		deploy_repo = os.path.join(temp_dir, 'git')
		refs_to_push = [args.branch]
		
		os.mkdir(work_dir)
		os.mkdir(deploy_dir)
		
		git_checkout(repo, work_dir, args.revision)
		maven_versions_set(work_dir, version)
		maven(work_dir, 'package') # Try to package the project in a separate step so that we fail before a tag is potentially created.
		
		if args.release:
			release_tag = 'release/{}'.format(version)
			refs_to_push.append(release_tag)
			
			git_tag(repo, release_tag, args.revision)
		
		if git_ref_exists(repo, args.branch):
			git_clone(repo, deploy_repo)
			git_checkout(deploy_repo, deploy_dir, args.branch)
			git_reset(deploy_repo, args.branch)
		else:
			git_init(deploy_repo)
		
		maven_deploy(work_dir, deploy_dir)
		
		git_commit_all(deploy_repo, deploy_dir, 'Deployment of version {}.'.format(version))
		git_push(deploy_repo, repo, ('HEAD', args.branch))
		
		if args.push:
			git_push(repo, 'origin', *refs_to_push)
	
	print('The project was successfully deployed.')


main()
