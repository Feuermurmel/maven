#! /usr/bin/env python3

import sys, os, re, subprocess, tempfile, argparse, contextlib


def log(msg, *args):
	print('{}: {}'.format(os.path.basename(sys.argv[0]), msg.format(*args)), file = sys.stderr)


class UserError(Exception):
	def __init__(self, msg, *args):
		super().__init__(msg.format(*args))


def num_sort_key(str):
	return re.sub('[0-9]+', lambda x: '%s0%s' % ('1' * len(x.group()), x.group()), str)


def command(*args, cwd = None, output = False, exit_code = False):
	stdout = subprocess.PIPE if output else None
	stderr = subprocess.PIPE if exit_code else None
	process = subprocess.Popen(args, cwd = cwd, stdout = stdout, stderr = stderr)
	out, _ = process.communicate()
	
	if exit_code:
		return process.returncode
	else:
		if process.returncode:
			raise UserError('Command failed with exit status {}: {}', process.returncode, ' '.join(args))
		
		return out


def maven(dir, goal, **properties):
	def iter_args():
		yield 'mvn'
		
		for k, v in properties.items():
			yield '-D{}={}'.format(k, v)
		
		yield goal
	
	command(*iter_args(), cwd = dir)


def maven_versions_set(dir, version):
	maven(dir, 'versions:set', newVersion = version)


def maven_deploy(dir, deploy_dir):
	maven(dir, 'deploy', **{ 'altDeploymentRepository': '-::default::file://{}'.format(os.path.abspath(deploy_dir)), 'maven.test.skip': 'true' })


def git(*args, git_dir = None, work_tree = None, output = False, exit_code = False, cwd = None):
	def iter_args():
		yield 'git'
		
		for k, v in ('git-dir', git_dir), ('work-tree', work_tree):
			if v:
				yield '--{}={}'.format(k, v)
		
		yield from args
	
	return command(*iter_args(), output = output, exit_code = exit_code, cwd = cwd)


def git_get_repo(dir):
	path, = git('rev-parse', '--git-dir', cwd = dir, output = True).decode().splitlines()
	
	return os.path.join(dir, path)


def git_name_rev(repo, rev):
	res, = git('name-rev', '--no-undefined', '--name-only', '--tags', rev, git_dir = repo, output = True).decode().splitlines()
	
	# Strip eventual '^0' suffix used on tags by newer versions of Git.
	res, *rest = res.rsplit('^', 1)
	
	if rest:
		rest, = rest
		
		assert rest == '0'
	
	return res


def git_tag(repo, tag, rev):
	git('tag', tag, rev, git_dir = repo)


def git_checkout(repo, work_tree, rev):
	git('checkout', rev, '.', git_dir = repo, work_tree = work_tree)


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
	parser = argparse.ArgumentParser(description = 'Deploy the project Maven project in the current working directory to the branch gh-pages in the repository which hosts this script.')
	parser.add_argument('revisions', nargs = '*', default = ['HEAD'], help = 'Revisions to deploy.')
	parser.add_argument('--release', metavar = 'version', default = None, help = 'Automatically create a tag for the current HEAD with this version number and push it to the remote `origin\'.')
	parser.add_argument('--branch', default = 'gh-pages', help = 'Branch to use as the Maven repository. Defaults to `gh-pages\'')
	parser.add_argument('--debug', action = 'store_true', help = 'Place temporary files in the current working directory and do not clean them up.')

	args = parser.parse_args()
	
	if not args.revisions and not args.release:
		raise UserError('At least one revision to deploy or --release must be specified.')
	
	return args


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
	project_repo = git_get_repo(os.getcwd())
	deploy_repo = git_get_repo(os.path.dirname(os.path.abspath(__file__)))
	revisions = args.revisions
	
	with make_temp_dir(args.debug) as temp_dir:
		if args.release:
			release_checkout_dir = os.path.join(temp_dir, 'release_checkout')
			revision = 'refs/tags/{}'.format(args.release)
			
			log('Deploying version {} ...', args.release)
			
			os.mkdir(release_checkout_dir)
			git_checkout(project_repo, release_checkout_dir, 'HEAD')
			maven(release_checkout_dir, 'package') # Try to package the project in a separate step so that we fail before a tag is potentially created.
			git_tag(project_repo, args.release, 'HEAD')
			git_push(project_repo, 'origin', revision)
			
			revisions.append(revision)
		
		project_repo_clone = os.path.join(temp_dir, 'project_repo')
		deploy_repo_clone = os.path.join(temp_dir, 'deploy_repo')
		deploy_repo_checkout = os.path.join(temp_dir, 'deploy_checkout')
		versions = []
		
		os.mkdir(deploy_repo_checkout)
		
		if git_ref_exists(deploy_repo, args.branch):
			git_clone(deploy_repo, deploy_repo_clone)
			git_checkout(deploy_repo_clone, deploy_repo_checkout, args.branch)
			git_reset(deploy_repo_clone, args.branch)
		else:
			git_init(deploy_repo_clone)
		
		# Clone the repository so we can check it out without the index getting messed up.
		git_clone(project_repo, project_repo_clone)
		
		for i, x in enumerate(args.revisions):
			deploy_checkout_dir = os.path.join(temp_dir, 'project_checkout_{}'.format(i))
			version = git_name_rev(project_repo_clone, x)
			
			log('Deploying version {} ...', version)
			
			os.mkdir(deploy_checkout_dir)
			git_checkout(project_repo_clone, deploy_checkout_dir, x)
			maven_versions_set(deploy_checkout_dir, version)
			maven_deploy(deploy_checkout_dir, deploy_repo_checkout)
			versions.append(version)
		
		git_commit_all(deploy_repo_clone, deploy_repo_checkout, 'Deployment of {} {}.'.format('versions' if len(versions) > 1 else 'version', ', '.join(sorted(versions, key = num_sort_key))))
		git_push(deploy_repo_clone, deploy_repo, ('HEAD', args.branch))
		git_push(deploy_repo, 'origin', args.branch)
	
	print('Deployment was successful.')


try:
	main()
except UserError as e:
	log('Error: {}', e)
	sys.exit(1)
except KeyboardInterrupt:
	log('Operation interrupted.')
	sys.exit(2)
