using System.Collections.ObjectModel;
using Avalonia.Controls;
using Avalonia.Platform.Storage;
using Avalonia.Threading;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using FluentAvalonia.UI.Controls;
using Injectio.Attributes;
using StabilityMatrix.Avalonia.Controls;
using StabilityMatrix.Avalonia.Services;
using StabilityMatrix.Avalonia.ViewModels.Base;
using StabilityMatrix.Core.Services;

namespace StabilityMatrix.Avalonia.ViewModels;

[RegisterSingleton<RemoteLibraryViewModel>]
public partial class RemoteLibraryViewModel : PageViewModelBase
{
    private readonly RemoteLibraryService service;
    private readonly ISettingsManager settings;
    private readonly IInferenceClientManager inference;
    private readonly DispatcherTimer pollTimer = new() { Interval = TimeSpan.FromSeconds(4) };
    public override string Title => "Remote library";
    public override IconSource IconSource => new SymbolIconSource { Symbol = Symbol.World };

    [ObservableProperty] private bool isEnabled;
    [ObservableProperty] private bool isBusy;
    [ObservableProperty] private string status = "Refresh to load the server library.";
    [ObservableProperty] private string serverLabel = "Remote server";
    [ObservableProperty] private string freeSpace = "";
    [ObservableProperty] private string downloadLocation = "";
    [ObservableProperty] private string search = "";
    [ObservableProperty] private RemotePackage? selectedPackage;
    [ObservableProperty] private RemoteModel? selectedModel;
    [ObservableProperty] private RemoteJob? selectedJob;
    [ObservableProperty] private RemoteChoice? selectedCatalog;
    [ObservableProperty] private RemoteChoice? selectedRoot;
    [ObservableProperty] private string installName = "ComfyUI Remote";
    [ObservableProperty] private string launchPort = "8196";
    [ObservableProperty] private string gpu = "";
    [ObservableProperty] private string launchArguments = "";
    [ObservableProperty] private string modelUrl = "";
    [ObservableProperty] private string modelToken = "";
    [ObservableProperty] private string modelDestination = "StableDiffusion/model.safetensors";
    [ObservableProperty] private string modelSha256 = "";

    public ObservableCollection<RemotePackage> Packages { get; } = [];
    public ObservableCollection<RemoteModel> Models { get; } = [];
    public ObservableCollection<RemoteJob> Jobs { get; } = [];
    public ObservableCollection<RemoteChoice> Catalog { get; } = [];
    public ObservableCollection<RemoteChoice> Roots { get; } = [];
    public IEnumerable<RemoteModel> FilteredModels => Models.Where(m =>
        string.IsNullOrWhiteSpace(Search) || m.Path.Contains(Search, StringComparison.OrdinalIgnoreCase));

    public RemoteLibraryViewModel(RemoteLibraryService service, ISettingsManager settings, IInferenceClientManager inference)
    {
        this.service = service;
        this.settings = settings;
        this.inference = inference;
        settings.RelayPropertyFor(this, vm => vm.IsEnabled, s => s.UseRemoteInference, true);
        pollTimer.Tick += async (_, _) =>
        {
            if (!IsBusy && Jobs.Any(j => j.Status is "Queued" or "Running"))
                await Refresh();
        };
    }

    partial void OnSearchChanged(string value) => OnPropertyChanged(nameof(FilteredModels));
    partial void OnSelectedPackageChanged(RemotePackage? value)
    {
        if (value is { Port: > 0 }) LaunchPort = value.Port.ToString();
    }
    partial void OnSelectedCatalogChanged(RemoteChoice? value)
    {
        if (value is not null) InstallName = value.Name + " Remote";
    }
    partial void OnSelectedRootChanged(RemoteChoice? value) => DownloadLocation = value?.Path ?? "";

    public override async Task OnLoadedAsync()
    {
        if (Design.IsDesignMode || !IsEnabled) return;
        pollTimer.Start();
        await Refresh();
    }

    public override void OnUnloaded()
    {
        pollTimer.Stop();
        base.OnUnloaded();
    }

    private static void Replace<T>(ObservableCollection<T> target, IEnumerable<T> source)
    {
        target.Clear();
        foreach (var item in source) target.Add(item);
    }

    private async Task LoadInventory()
    {
        var data = await service.RequestAsync<RemoteInventory>(new() { ["action"] = "inventory" });
        var packageId = SelectedPackage?.Id;
        var modelKey = SelectedModel is { } m ? m.Root + ":" + m.Path : null;
        var catalogId = SelectedCatalog?.Id;
        var rootId = SelectedRoot?.Id;
        var jobId = SelectedJob?.Id;
        Replace(Packages, data.Packages);
        Replace(Models, data.Models);
        Replace(Jobs, data.Jobs);
        Replace(Catalog, data.Catalog);
        Replace(Roots, data.Roots);
        SelectedPackage = Packages.FirstOrDefault(p => p.Id == packageId);
        SelectedModel = Models.FirstOrDefault(p => p.Root + ":" + p.Path == modelKey);
        SelectedCatalog = Catalog.FirstOrDefault(p => p.Id == catalogId) ?? Catalog.FirstOrDefault();
        SelectedRoot = Roots.FirstOrDefault(p => p.Id == rootId) ?? Roots.FirstOrDefault(p => p.Id == "downloads") ?? Roots.FirstOrDefault();
        SelectedJob = Jobs.FirstOrDefault(p => p.Id == jobId);
        OnPropertyChanged(nameof(FilteredModels));
        ServerLabel = $"{settings.Settings.RemoteSshHost} · {data.Library}";
        FreeSpace = $"{data.FreeBytes / 1073741824d:N1} GB free · {Models.Count} model files · {Packages.Count} packages";
        Status = $"Connected to {settings.Settings.RemoteSshHost}";
    }

    [RelayCommand]
    private async Task Refresh()
    {
        if (IsBusy || !IsEnabled) return;
        IsBusy = true;
        try { await LoadInventory(); }
        catch (Exception ex) { Status = ex.Message; }
        finally { IsBusy = false; }
    }

    private async Task Submit(Dictionary<string, object?> request)
    {
        if (IsBusy) return;
        IsBusy = true;
        try
        {
            request["action"] = "job";
            var result = await service.RequestAsync<RemoteResult>(request);
            await LoadInventory();
            Status = result.Message;
            pollTimer.Start();
        }
        catch (Exception ex) { Status = ex.Message; }
        finally { IsBusy = false; }
    }

    [RelayCommand]
    private Task InstallPackage() => SelectedCatalog is null ? Task.CompletedTask : Submit(new()
    { ["operation"] = "install", ["kind"] = SelectedCatalog.Id, ["name"] = InstallName });

    [RelayCommand]
    private Task StartPackage()
    {
        if (SelectedPackage is null) { Status = "Select a package first."; return Task.CompletedTask; }
        if (!int.TryParse(LaunchPort, out var port)) { Status = "Enter a valid port number."; return Task.CompletedTask; }
        return Submit(new() { ["operation"] = "start", ["packageId"] = SelectedPackage.Id,
            ["port"] = port, ["gpu"] = Gpu, ["arguments"] = LaunchArguments });
    }

    [RelayCommand]
    private Task StopPackage() => SelectedPackage is null ? Task.CompletedTask : Submit(new()
    { ["operation"] = "stop", ["packageId"] = SelectedPackage.Id });

    [RelayCommand]
    private Task UpdatePackage() => SelectedPackage is null ? Task.CompletedTask : Submit(new()
    { ["operation"] = "update", ["packageId"] = SelectedPackage.Id });

    private int SelectedPort() => SelectedPackage is { Port: > 0 } package ? package.Port
        : int.TryParse(LaunchPort, out var port) ? port : throw new ArgumentException("Enter the server port.");

    [RelayCommand]
    private async Task OpenWebUi()
    {
        if (SelectedPackage is null) return;
        try
        {
            var address = await service.ForwardPortAsync(SelectedPort());
            StabilityMatrix.Core.Processes.ProcessRunner.OpenUrl(address.ToString());
        }
        catch (Exception ex) { Status = ex.Message; }
    }

    [RelayCommand]
    private async Task UseForInference()
    {
        if (SelectedPackage?.Kind != "ComfyUI") { Status = "Inference requires a ComfyUI package."; return; }
        if (IsBusy) return;
        IsBusy = true;
        try
        {
            var address = await service.ForwardPortAsync(SelectedPort());
            await inference.CloseAsync();
            await inference.ConnectAsync(address);
            Status = $"Inference connected to {SelectedPackage.Name} on {settings.Settings.RemoteSshHost}:{SelectedPort()}";
        }
        catch (Exception ex) { Status = ex.Message; }
        finally { IsBusy = false; }
    }

    [RelayCommand]
    private async Task DownloadModel()
    {
        if (SelectedRoot is null) return;
        var request = new Dictionary<string, object?> { ["operation"] = "download", ["url"] = ModelUrl,
            ["root"] = SelectedRoot.Id, ["path"] = ModelDestination, ["sha256"] = ModelSha256, ["token"] = ModelToken };
        await Submit(request);
        ModelToken = "";
    }

    [RelayCommand(IncludeCancelCommand = true)]
    private async Task UploadModel(CancellationToken cancellationToken)
    {
        if (IsBusy || SelectedRoot is null) return;
        var files = await App.StorageProvider.OpenFilePickerAsync(new FilePickerOpenOptions { Title = "Upload model to server", AllowMultiple = false });
        if (files.Count == 0 || files[0].TryGetLocalPath() is not { } path) return;
        IsBusy = true;
        try
        {
            Status = $"Uploading {Path.GetFileName(path)} to {settings.Settings.RemoteSshHost}…";
            await using var stream = File.OpenRead(path);
            var result = await service.RequestAsync<RemoteResult>(new() { ["action"] = "upload",
                ["root"] = SelectedRoot.Id, ["path"] = ModelDestination, ["size"] = stream.Length,
                ["sha256"] = ModelSha256 }, cancellationToken, stream);
            await LoadInventory();
            Status = result.Message;
        }
        catch (OperationCanceledException) { Status = "Upload cancelled."; }
        catch (Exception ex) { Status = ex.Message; }
        finally { IsBusy = false; }
    }

    [RelayCommand]
    private async Task MoveModel()
    {
        if (SelectedModel is not { } model) return;
        var field = new TextBox { Text = model.Path, MinWidth = 450 };
        var dialog = new BetterContentDialog { Title = "Rename or move model on server", Content = field,
            PrimaryButtonText = "Move", CloseButtonText = "Cancel" };
        if (await dialog.ShowAsync() != ContentDialogResult.Primary) return;
        await Submit(new() { ["operation"] = "move", ["root"] = model.Root, ["path"] = model.Path, ["destination"] = field.Text });
    }

    [RelayCommand]
    private async Task TrashModel()
    {
        if (SelectedModel is not { } model) return;
        var dialog = new BetterContentDialog { Title = "Move model to server trash?",
            Content = $"{model.Name}\nIts metadata will move with it. Use Restore last trashed model to undo.",
            PrimaryButtonText = "Move to trash", CloseButtonText = "Cancel" };
        if (await dialog.ShowAsync() != ContentDialogResult.Primary) return;
        await Submit(new() { ["operation"] = "trash", ["root"] = model.Root, ["path"] = model.Path });
    }

    [RelayCommand]
    private Task RestoreModel() => Submit(new() { ["operation"] = "restore" });

    private async Task ShowLogs(Dictionary<string, object?> request)
    {
        try
        {
            request["action"] = "logs";
            var result = await service.RequestAsync<RemoteResult>(request);
            await new BetterContentDialog { Title = "Server log", CloseButtonText = "Close",
                Content = new TextBox { Text = result.Message, IsReadOnly = true, AcceptsReturn = true,
                    Width = 700, Height = 430, TextWrapping = global::Avalonia.Media.TextWrapping.Wrap } }.ShowAsync();
        }
        catch (Exception ex) { Status = ex.Message; }
    }

    [RelayCommand]
    private Task PackageLogs() => SelectedPackage is null ? Task.CompletedTask : ShowLogs(new() { ["packageId"] = SelectedPackage.Id });
    [RelayCommand]
    private Task JobLogs() => SelectedJob is null ? Task.CompletedTask : ShowLogs(new() { ["jobId"] = SelectedJob.Id });

    [RelayCommand]
    private async Task CancelJob()
    {
        if (SelectedJob is null) return;
        try
        {
            var result = await service.RequestAsync<RemoteResult>(new() { ["action"] = "cancel", ["jobId"] = SelectedJob.Id });
            Status = result.Message;
        }
        catch (Exception ex) { Status = ex.Message; }
    }
}
