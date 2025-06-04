import multiprocessing


multiprocessing.freeze_support()


if __name__ == "__main__":
    from audible_cli import cli

    cli.main()
