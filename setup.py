from setuptools import setup

with open("README.md", "r") as fh:
    long_description = fh.read()


setup(name='linger',
      version='1.0.0',
      description='Message queue and pubsub service with HTTP API',
      long_description=long_description,
      long_description_content_type="text/markdown",
      author='Jacob Svensson',
      author_email='jacob@nephics.com',
      license="http://www.apache.org/licenses/LICENSE-2.0",
      url='https://github.com/nephics/linger',
      packages=['linger'],
      entry_points={
        'console_scripts': [
          'linger = linger:main'
        ],
      },
      install_requires=['tornado>=4.5.2'],
      classifiers=[
          'Development Status :: 5 - Production/Stable',
          'Intended Audience :: Developers',
          'License :: OSI Approved :: Apache Software License',
          'Programming Language :: Python :: 3',
          'Topic :: System :: Distributed Computing',
          'Topic :: System :: Networking',
          'Topic :: Utilities'
      ])
